"""Datenbankzugriff fuer die Ausgabenverwaltung.

Kapselt Verbindungsaufbau, Schema-Erstellung und alle SQL-Abfragen.
Bewusst ohne ORM gehalten, damit die App ohne zusaetzliche Abhaengigkeiten
(ausser Flask) auskommt.
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "ausgaben.db"

# Major.Minor.Patch. Die Major-Zahl ist die Schema-Version: sie MUSS erhoeht
# werden, sobald SCHEMA/SCHEMA_INDEXES oder _migrate() sich aendern (neue
# Tabelle/Spalte, geaenderte Constraints). Minor/Patch sind reine
# Code-Aenderungen ohne Schema-Wirkung. is_compatible() vergleicht nur die
# Major mit der in schema_meta gespeicherten DB-Version - ein Major-Sprung
# sperrt die App, bis migrate() explizit aufgerufen wurde (siehe unten).
#
# 2.0.0: Nutzerverwaltung - neue users-Tabelle, user_id-FK an accounts/
# transactions, categories neu mit UNIQUE(user_id, name) statt UNIQUE(name)
# (zwei Nutzer duerfen je eine "Miete" haben). Siehe _migrate() fuer den
# dafuer noetigen Tabellen-Rebuild von categories.
# 2.0.1: App umbenannt von "Hauptbuch" zu "Klarcash" (nur Anzeige/Branding,
# keine Schema-Aenderung).
APP_VERSION = "2.0.1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'user')) DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('Konto', 'Bar', 'Anlage')),
    initial_balance REAL NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- name ist NICHT mehr global eindeutig, sondern nur je Nutzer
-- (UNIQUE(user_id, name)) - siehe APP_VERSION-Kommentar oben.
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('Einnahme', 'Ausgabe')),
    parent_id INTEGER REFERENCES categories(id),
    user_id INTEGER REFERENCES users(id),
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    type TEXT NOT NULL CHECK(type IN ('Einnahme', 'Ausgabe', 'Umbuchung')),
    category_id INTEGER REFERENCES categories(id),
    description TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL CHECK(amount > 0),
    target_account_id INTEGER REFERENCES accounts(id),
    deleted INTEGER NOT NULL DEFAULT 0,
    deleted_at TEXT,
    user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS transaction_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id),
    description TEXT NOT NULL DEFAULT '',
    amount REAL NOT NULL,
    category_id INTEGER REFERENCES categories(id),
    position INTEGER NOT NULL DEFAULT 0
);
"""

SCHEMA_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_tx_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_account ON transactions(account_id);
CREATE INDEX IF NOT EXISTS idx_tx_deleted ON transactions(deleted);
CREATE INDEX IF NOT EXISTS idx_tx_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_cat_parent ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_cat_user ON categories(user_id);
CREATE INDEX IF NOT EXISTS idx_items_tx ON transaction_items(transaction_id);
CREATE INDEX IF NOT EXISTS idx_accounts_user ON accounts(user_id);
"""

DEFAULT_CATEGORIES = [
    ("Gehalt", "Einnahme"),
    ("Sonstige Einnahmen", "Einnahme"),
    ("Miete", "Ausgabe"),
    ("Lebensmittel", "Ausgabe"),
    ("Versicherung", "Ausgabe"),
    ("Freizeit", "Ausgabe"),
    ("Sonstige Ausgaben", "Ausgabe"),
]

PAGE_SIZE = 25

# Fasst jede Buchung als virtuellen "Posten" auf: Buchungen ohne eigene
# Posten liefern genau eine Zeile (ihre eigenen Werte), Buchungen mit
# Posten liefern eine Zeile je Posten. So koennen Kategorie-Auswertungen
# transparent sowohl einfache Buchungen als auch aufgeteilte Kassenzettel
# beruecksichtigen, ohne dass sich an Kontostand-Berechnungen etwas aendert
# (die weiterhin ausschliesslich transactions.amount verwenden).
FLATTENED_CTE = """
WITH flat AS (
    SELECT t.id AS tx_id, t.date AS date, t.account_id AS account_id,
           t.target_account_id AS target_account_id, t.type AS type,
           t.deleted AS deleted, t.category_id AS category_id, t.amount AS amount,
           t.user_id AS user_id
    FROM transactions t
    WHERE NOT EXISTS (SELECT 1 FROM transaction_items i WHERE i.transaction_id = t.id)
    UNION ALL
    SELECT t.id, t.date, t.account_id, t.target_account_id, t.type, t.deleted,
           i.category_id, i.amount, t.user_id
    FROM transactions t
    JOIN transaction_items i ON i.transaction_id = t.id
)
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _ensure_meta(conn):
    """Legt schema_meta an, falls die DB noch aus einer Zeit vor der
    Versionierung stammt. CREATE TABLE IF NOT EXISTS genuegt hier, da an
    dieser Tabelle selbst nie Spalten nachgezogen werden muessen."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )


def db_version(conn):
    """Liefert die in der DB gespeicherte Version, oder None, wenn die DB
    noch nie via init_db()/migrate() versioniert wurde (Alt-Installation von
    vor der Versionierung)."""
    _ensure_meta(conn)
    row = conn.execute("SELECT value FROM schema_meta WHERE key = 'version'").fetchone()
    return row["value"] if row else None


def _set_version(conn, version):
    conn.execute(
        """
        INSERT INTO schema_meta (key, value) VALUES ('version', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (version,),
    )


def major(version):
    """Extrahiert die Major-Zahl aus 'a.b.c'. None bei fehlender/kaputter Version."""
    if not version:
        return None
    try:
        return int(version.split(".")[0])
    except ValueError:
        return None


def is_compatible(conn):
    """True, wenn die Major-Version der DB zur Major-Version des Codes passt.
    Eine DB ohne gespeicherte Version (Alt-Installation oder frisch von
    init_db() angelegt, aber noch nicht migriert) gilt als NICHT kompatibel,
    bis migrate() explizit gelaufen ist."""
    return major(db_version(conn)) == major(APP_VERSION)


def _core_tables_exist(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM sqlite_master WHERE type = 'table' AND name = 'transactions'"
    ).fetchone()
    return row["c"] > 0


def _migrate(conn):
    """Ergaenzt Spalten, die in aelteren Datenbankversionen noch fehlen.
    Wird ausschliesslich von migrate() aufgerufen, nie automatisch von
    init_db() - siehe migrate()-Docstring fuer die Begruendung.

    foreign_keys UND legacy_alter_table werden waehrend des categories-
    Rebuilds (siehe unten) bewusst umgeschaltet: ALTER TABLE ... RENAME TO
    schreibt REFERENCES-Klauseln in anderen Tabellen (hier: transactions,
    transaction_items) automatisch auf den Zwischennamen "categories_old" um,
    sofern foreign_keys eingeschaltet ist ODER legacy_alter_table ausgeschaltet
    ist (SQLite-Default) - erst BEIDE zusammen (foreign_keys=OFF UND
    legacy_alter_table=ON) unterdruecken das Umschreiben zuverlaessig. Jede
    neue Verbindung (get_connection()) setzt foreign_keys ohnehin wieder ON,
    der Effekt bleibt also auf diese Migrations-Verbindung beschraenkt."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("PRAGMA legacy_alter_table = ON")

    tx_cols = [r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    if "deleted" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    if "deleted_at" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN deleted_at TEXT")
    if "user_id" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN user_id INTEGER REFERENCES users(id)")

    acc_cols = [r["name"] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    if "user_id" not in acc_cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN user_id INTEGER REFERENCES users(id)")

    cat_cols = [r["name"] for r in conn.execute("PRAGMA table_info(categories)").fetchall()]
    if "user_id" not in cat_cols:
        # Das globale UNIQUE(name) muss zu UNIQUE(user_id, name) werden, damit
        # zwei Nutzer je eine gleichnamige Kategorie haben duerfen. SQLite
        # kann ein spaltengebundenes UNIQUE nicht per ALTER TABLE aendern ->
        # Standard-Rebuild (rename, neu anlegen, Daten kopieren, alte droppen).
        # Alt-Kategorien bleiben mit user_id = NULL ("verwaist"), bis sie
        # ueber claim_orphan_data() einem Nutzer zugeordnet werden.
        has_parent_id = "parent_id" in cat_cols
        conn.execute("ALTER TABLE categories RENAME TO categories_old")
        conn.execute(
            """
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('Einnahme', 'Ausgabe')),
                parent_id INTEGER REFERENCES categories(id),
                user_id INTEGER REFERENCES users(id),
                UNIQUE(user_id, name)
            )
            """
        )
        parent_expr = "parent_id" if has_parent_id else "NULL"
        conn.execute(
            f"""
            INSERT INTO categories (id, name, kind, parent_id, user_id)
            SELECT id, name, kind, {parent_expr}, NULL FROM categories_old
            """
        )
        conn.execute("DROP TABLE categories_old")
    elif "parent_id" not in cat_cols:
        conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id)")

    conn.execute("PRAGMA legacy_alter_table = OFF")
    conn.execute("PRAGMA foreign_keys = ON")


def seed_categories_for_user(conn, user_id):
    """Legt die Standardkategorien fuer einen (neuen) Nutzer an. INSERT OR
    IGNORE, damit ein Aufruf nach claim_orphan_data() - der Nutzer hat dann
    ggf. schon gleichnamige uebernommene Alt-Kategorien - keinen
    UNIQUE(user_id, name)-Konflikt wirft."""
    conn.executemany(
        "INSERT OR IGNORE INTO categories (name, kind, user_id) VALUES (?, ?, ?)",
        [(name, kind, user_id) for name, kind in DEFAULT_CATEGORIES],
    )


def init_db():
    """Legt das Schema fuer eine FRISCHE Datenbank an (bewusst OHNE Konten
    oder Standardkategorien - beides ist erst sinnvoll, sobald ein Nutzer
    existiert. Die Ersteinrichtung ruft dafuer seed_categories_for_user() mit
    der ID des ersten Admin-Nutzers auf).

    Hebt eine BESTEHENDE Datenbank bewusst NICHT auf das aktuelle Schema an -
    das ist ausschliesslich Aufgabe von migrate(). So bleibt eine Alt-DB nach
    einem Code-Update inkompatibel (is_compatible() liefert False), bis die
    Migration bewusst ausgeloest wird (CLI --migrate oder Route /migration)."""
    with db_session() as conn:
        _ensure_meta(conn)
        if _core_tables_exist(conn):
            return  # bestehende DB: nichts anfassen, migrate() ist zustaendig
        conn.executescript(SCHEMA)
        conn.executescript(SCHEMA_INDEXES)
        _set_version(conn, APP_VERSION)


def migrate():
    """Expliziter Migrationsschritt: hebt eine bestehende Datenbank auf das
    aktuelle Schema an UND stempelt sie auf APP_VERSION. Muss bewusst
    ausgeloest werden (CLI --migrate oder GET/POST /migration) - init_db()
    tut dies absichtlich nicht mehr automatisch (siehe dessen Docstring)."""
    with db_session() as conn:
        _ensure_meta(conn)
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.executescript(SCHEMA_INDEXES)
        _set_version(conn, APP_VERSION)


# ------------------------------------------------------------- Nutzer

def create_user(conn, username, password_hash, role="user"):
    cur = conn.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
        (username, password_hash, role),
    )
    return cur.lastrowid


def get_user_by_username(conn, username):
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def get_user_by_id(conn, user_id):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def list_users(conn):
    return conn.execute("SELECT * FROM users ORDER BY username").fetchall()


def user_count(conn):
    return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]


def delete_user(conn, user_id):
    """Entfernt einen Nutzer. Dessen Kategorien werden automatisch mit
    geloescht: jeder Nutzer bekommt bei der Anlage sofort die 7 Standard-
    kategorien (seed_categories_for_user), ein "datenloser" Nutzer haette
    also nie loeschbar sein koennen, wenn Kategorien mitzaehlen wuerden.
    Ohne Buchungen sind sie unbedenklich - user_has_data() (siehe unten)
    stellt vor dem Aufruf sicher, dass keine Konten/Buchungen mehr existieren,
    also auch keine transaction_items auf diese Kategorien verweisen."""
    conn.execute("DELETE FROM categories WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))


def set_password(conn, user_id, password_hash):
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))


def user_has_data(conn, user_id):
    """Prueft, ob ein Nutzer noch eigene Konten oder Buchungen hat. Kategorien
    zaehlen bewusst NICHT: die automatisch angelegten Standardkategorien
    wuerden eine Loeschung sonst immer blockieren (siehe delete_user())."""
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM accounts WHERE user_id = ?) +
            (SELECT COUNT(*) FROM transactions WHERE user_id = ?) AS c
        """,
        (user_id, user_id),
    ).fetchone()
    return row["c"] > 0


def claim_orphan_data(conn, user_id):
    """Ordnet 'verwaiste' Daten (user_id IS NULL) einem Nutzer zu - das sind
    typischerweise Alt-Buchungen/-Konten/-Kategorien aus einer Datenbank, die
    von vor Version 2.0.0 migriert wurde. Wird von der Ersteinrichtung fuer
    den ersten (Admin-)Nutzer aufgerufen, VOR seed_categories_for_user(), damit
    uebernommene Alt-Kategorien nicht durch Standardkategorien dupliziert
    werden (seed_categories_for_user nutzt dafuer INSERT OR IGNORE)."""
    conn.execute("UPDATE accounts SET user_id = ? WHERE user_id IS NULL", (user_id,))
    conn.execute("UPDATE categories SET user_id = ? WHERE user_id IS NULL", (user_id,))
    conn.execute("UPDATE transactions SET user_id = ? WHERE user_id IS NULL", (user_id,))


def account_balance(conn, account_id, user_id):
    row = conn.execute(
        """
        SELECT
            a.initial_balance
            + COALESCE((SELECT SUM(amount) FROM transactions
                        WHERE account_id = a.id AND type = 'Einnahme' AND deleted = 0 AND user_id = a.user_id), 0)
            - COALESCE((SELECT SUM(amount) FROM transactions
                        WHERE account_id = a.id AND type = 'Ausgabe' AND deleted = 0 AND user_id = a.user_id), 0)
            - COALESCE((SELECT SUM(amount) FROM transactions
                        WHERE account_id = a.id AND type = 'Umbuchung' AND deleted = 0 AND user_id = a.user_id), 0)
            + COALESCE((SELECT SUM(amount) FROM transactions
                        WHERE target_account_id = a.id AND type = 'Umbuchung' AND deleted = 0 AND user_id = a.user_id), 0)
            AS balance
        FROM accounts a WHERE a.id = ? AND a.user_id = ?
        """,
        (account_id, user_id),
    ).fetchone()
    return row["balance"] if row else 0.0


def accounts_with_balances(conn, user_id, include_archived=False):
    query = "SELECT * FROM accounts WHERE user_id = ?"
    params = [user_id]
    if not include_archived:
        query += " AND archived = 0"
    query += " ORDER BY type, name"
    accounts = conn.execute(query, params).fetchall()
    result = []
    for acc in accounts:
        d = dict(acc)
        d["balance"] = account_balance(conn, acc["id"], user_id)
        result.append(d)
    return result


def total_balance(conn, user_id):
    accounts = accounts_with_balances(conn, user_id)
    return sum(a["balance"] for a in accounts)


# --------------------------------------------------------- Kategorien

def category_tree(conn, user_id, kind=None):
    """Kategorien als zweistufiger Baum (Hauptkategorie -> Unterkategorien).

    Rueckgabe: Liste von dicts der Hauptkategorien (parent_id IS NULL),
    jede mit einem zusaetzlichen Schluessel "children" (Liste von dicts).
    Optional gefiltert nach kind ('Einnahme'/'Ausgabe').
    """
    query = "SELECT * FROM categories WHERE user_id = ?"
    params = [user_id]
    if kind:
        query += " AND kind = ?"
        params.append(kind)
    rows = [dict(r) for r in conn.execute(query, params).fetchall()]

    by_id = {r["id"]: r for r in rows}
    for r in rows:
        r["children"] = []

    top = []
    for r in rows:
        parent = by_id.get(r["parent_id"]) if r["parent_id"] else None
        if parent is not None:
            parent["children"].append(r)
        else:
            top.append(r)

    top.sort(key=lambda r: r["name"])
    for r in rows:
        r["children"].sort(key=lambda c: c["name"])
    return top


def category_in_use(conn, cat_id, user_id):
    """Prueft, ob eine Kategorie noch verwendet wird - sowohl direkt an
    einer Buchung als auch an einem einzelnen Posten innerhalb einer
    aufgeteilten Buchung."""
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM transactions WHERE category_id = ? AND user_id = ?) +
            (SELECT COUNT(*) FROM transaction_items i
                JOIN transactions t ON t.id = i.transaction_id
                WHERE i.category_id = ? AND t.user_id = ?) AS c
        """,
        (cat_id, user_id, cat_id, user_id),
    ).fetchone()
    return row["c"] > 0


def category_has_children(conn, cat_id, user_id):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM categories WHERE parent_id = ? AND user_id = ?", (cat_id, user_id)
    ).fetchone()
    return row["c"] > 0


# ----------------------------------------------------- Buchungsposten

def get_items(conn, transaction_id, user_id):
    return conn.execute(
        """
        SELECT i.*,
               COALESCE(CASE WHEN p.name IS NOT NULL THEN p.name || ' \u203a ' || c.name ELSE c.name END, NULL) AS category_name
        FROM transaction_items i
        JOIN transactions t ON t.id = i.transaction_id
        LEFT JOIN categories c ON c.id = i.category_id
        LEFT JOIN categories p ON p.id = c.parent_id
        WHERE i.transaction_id = ? AND t.user_id = ?
        ORDER BY i.position, i.id
        """,
        (transaction_id, user_id),
    ).fetchall()


def replace_items(conn, transaction_id, items):
    """Ersetzt alle Posten einer Buchung. items: Liste von
    (description, amount, category_id)-Tupeln. Eine leere Liste entfernt
    alle Posten (Buchung wird wieder zu einer einfachen Einzelbuchung)."""
    conn.execute("DELETE FROM transaction_items WHERE transaction_id = ?", (transaction_id,))
    for position, (description, amount, category_id) in enumerate(items):
        conn.execute(
            """
            INSERT INTO transaction_items (transaction_id, description, amount, category_id, position)
            VALUES (?, ?, ?, ?, ?)
            """,
            (transaction_id, description, amount, category_id, position),
        )


def delete_items(conn, transaction_id):
    conn.execute("DELETE FROM transaction_items WHERE transaction_id = ?", (transaction_id,))


# ------------------------------------------------------------- Berichte

def grouped_report(conn, group_expr, joins, where_sql, params, use_items=False):
    """Fuehrt eine gruppierte Auswertung ueber transactions aus.

    group_expr: SQL-Ausdruck, der den Gruppierungsschluessel liefert
                (z. B. "t.type" oder "strftime('%Y-%m', t.date)").
    joins: zusaetzliche JOIN-Klauseln, die group_expr benoetigt (kann leer sein).
    where_sql: vollstaendige WHERE-Klausel inkl. "WHERE ...", bezogen auf
               die Spalten date/account_id/target_account_id/type/deleted
               (identisch benannt, egal ob use_items an oder aus ist).
    params: Parameter fuer die Platzhalter in where_sql.
    use_items: True fuer Gruppierungen, die auf Posten-Ebene aufloesen
               sollen (Kategorie/Hauptkategorie) - zaehlt "Anzahl" dabei
               weiterhin pro Buchung, nicht pro Posten.

    Rueckgabe: Rows mit grp, einnahmen, ausgaben, anzahl.
    """
    if use_items:
        query = f"""
            {FLATTENED_CTE}
            SELECT {group_expr} AS grp,
                   COALESCE(SUM(CASE WHEN t.type = 'Einnahme' THEN t.amount END), 0) AS einnahmen,
                   COALESCE(SUM(CASE WHEN t.type = 'Ausgabe' THEN t.amount END), 0) AS ausgaben,
                   COUNT(DISTINCT t.tx_id) AS anzahl
            FROM flat t
            {joins}
            {where_sql}
            GROUP BY grp
        """
    else:
        query = f"""
            SELECT {group_expr} AS grp,
                   COALESCE(SUM(CASE WHEN t.type = 'Einnahme' THEN t.amount END), 0) AS einnahmen,
                   COALESCE(SUM(CASE WHEN t.type = 'Ausgabe' THEN t.amount END), 0) AS ausgaben,
                   COUNT(*) AS anzahl
            FROM transactions t
            {joins}
            {where_sql}
            GROUP BY grp
        """
    return conn.execute(query, params).fetchall()


# --------------------------------------------------------- Statistiken

def monthly_summary(conn, start_date, user_id):
    """Einnahmen/Ausgaben je Kalendermonat ab start_date (YYYY-MM-DD).

    Rueckgabe: dict {"YYYY-MM": {"einnahmen": x, "ausgaben": y}}
    """
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m', date) AS ym,
               COALESCE(SUM(CASE WHEN type = 'Einnahme' THEN amount END), 0) AS einnahmen,
               COALESCE(SUM(CASE WHEN type = 'Ausgabe' THEN amount END), 0) AS ausgaben
        FROM transactions
        WHERE date >= ? AND deleted = 0 AND user_id = ?
        GROUP BY ym
        """,
        (start_date, user_id),
    ).fetchall()
    return {r["ym"]: {"einnahmen": r["einnahmen"], "ausgaben": r["ausgaben"]} for r in rows}


def category_breakdown(conn, tx_type, start_date, user_id):
    """Summen je Kategorie fuer Einnahme/Ausgabe ab start_date, absteigend sortiert.

    Beruecksichtigt Posten: Buchungen mit Kassenzettel-Posten werden nach
    deren jeweiliger Kategorie aufgeteilt statt als eine Buchung gezaehlt.
    Unterkategorien werden als "Hauptkategorie \u203a Unterkategorie" ausgewiesen.
    """
    rows = conn.execute(
        FLATTENED_CTE + """
        SELECT COALESCE(
                   CASE WHEN p.name IS NOT NULL THEN p.name || ' \u203a ' || c.name ELSE c.name END,
                   'Ohne Kategorie'
               ) AS category,
               SUM(flat.amount) AS summe
        FROM flat
        LEFT JOIN categories c ON c.id = flat.category_id
        LEFT JOIN categories p ON p.id = c.parent_id
        WHERE flat.type = ? AND flat.date >= ? AND flat.deleted = 0 AND flat.user_id = ?
        GROUP BY category
        ORDER BY summe DESC
        """,
        (tx_type, start_date, user_id),
    ).fetchall()
    return rows


def earliest_transaction_date(conn, user_id):
    row = conn.execute(
        "SELECT MIN(date) AS d FROM transactions WHERE deleted = 0 AND user_id = ?", (user_id,)
    ).fetchone()
    return row["d"]


def net_worth_series(conn, user_id):
    """Kumulierter Gesamtbestand ueber die Zeit (nur aktive Konten).

    Umbuchungen wirken sich per Definition nicht auf die Summe aus.
    Rueckgabe: (start_total, [{"date": ..., "total": ...}, ...])
    """
    start_total = conn.execute(
        "SELECT COALESCE(SUM(initial_balance), 0) AS s FROM accounts WHERE archived = 0 AND user_id = ?",
        (user_id,),
    ).fetchone()["s"]

    rows = conn.execute(
        """
        SELECT t.date AS date,
               SUM(CASE WHEN t.type = 'Einnahme' THEN t.amount
                        WHEN t.type = 'Ausgabe' THEN -t.amount
                        ELSE 0 END) AS net
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE a.archived = 0 AND t.deleted = 0 AND t.user_id = ?
        GROUP BY t.date
        ORDER BY t.date
        """,
        (user_id,),
    ).fetchall()

    series = []
    running = start_total
    for r in rows:
        running += r["net"]
        series.append({"date": r["date"], "total": running})
    return start_total, series
