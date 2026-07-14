"""Datenbankzugriff fuer die Ausgabenverwaltung.

Kapselt Verbindungsaufbau, Schema-Erstellung und alle SQL-Abfragen.
Bewusst ohne ORM gehalten, damit die App ohne zusaetzliche Abhaengigkeiten
(ausser Flask) auskommt.
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).parent / "ausgaben.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK(type IN ('Konto', 'Bar', 'Anlage')),
    initial_balance REAL NOT NULL DEFAULT 0,
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK(kind IN ('Einnahme', 'Ausgabe')),
    parent_id INTEGER REFERENCES categories(id)
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
CREATE INDEX IF NOT EXISTS idx_cat_parent ON categories(parent_id);
CREATE INDEX IF NOT EXISTS idx_items_tx ON transaction_items(transaction_id);
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
           t.deleted AS deleted, t.category_id AS category_id, t.amount AS amount
    FROM transactions t
    WHERE NOT EXISTS (SELECT 1 FROM transaction_items i WHERE i.transaction_id = t.id)
    UNION ALL
    SELECT t.id, t.date, t.account_id, t.target_account_id, t.type, t.deleted,
           i.category_id, i.amount
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


def _migrate(conn):
    """Ergaenzt Spalten, die in aelteren Datenbankversionen noch fehlen."""
    tx_cols = [r["name"] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    if "deleted" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
    if "deleted_at" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN deleted_at TEXT")

    cat_cols = [r["name"] for r in conn.execute("PRAGMA table_info(categories)").fetchall()]
    if "parent_id" not in cat_cols:
        conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id)")


def init_db():
    """Legt das Schema an. Erzeugt Standardkategorien, aber bewusst KEINE
    Konten - der erste Kontostand soll eine explizite Nutzerentscheidung
    sein, kein geratener Platzhalter."""
    with db_session() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.executescript(SCHEMA_INDEXES)
        existing = conn.execute("SELECT COUNT(*) AS c FROM categories").fetchone()["c"]
        if existing == 0:
            conn.executemany(
                "INSERT INTO categories (name, kind) VALUES (?, ?)", DEFAULT_CATEGORIES
            )


def account_balance(conn, account_id):
    row = conn.execute(
        """
        SELECT
            a.initial_balance
            + COALESCE((SELECT SUM(amount) FROM transactions
                        WHERE account_id = a.id AND type = 'Einnahme' AND deleted = 0), 0)
            - COALESCE((SELECT SUM(amount) FROM transactions
                        WHERE account_id = a.id AND type = 'Ausgabe' AND deleted = 0), 0)
            - COALESCE((SELECT SUM(amount) FROM transactions
                        WHERE account_id = a.id AND type = 'Umbuchung' AND deleted = 0), 0)
            + COALESCE((SELECT SUM(amount) FROM transactions
                        WHERE target_account_id = a.id AND type = 'Umbuchung' AND deleted = 0), 0)
            AS balance
        FROM accounts a WHERE a.id = ?
        """,
        (account_id,),
    ).fetchone()
    return row["balance"] if row else 0.0


def accounts_with_balances(conn, include_archived=False):
    query = "SELECT * FROM accounts"
    if not include_archived:
        query += " WHERE archived = 0"
    query += " ORDER BY type, name"
    accounts = conn.execute(query).fetchall()
    result = []
    for acc in accounts:
        d = dict(acc)
        d["balance"] = account_balance(conn, acc["id"])
        result.append(d)
    return result


def total_balance(conn):
    accounts = accounts_with_balances(conn)
    return sum(a["balance"] for a in accounts)


# --------------------------------------------------------- Kategorien

def category_tree(conn, kind=None):
    """Kategorien als zweistufiger Baum (Hauptkategorie -> Unterkategorien).

    Rueckgabe: Liste von dicts der Hauptkategorien (parent_id IS NULL),
    jede mit einem zusaetzlichen Schluessel "children" (Liste von dicts).
    Optional gefiltert nach kind ('Einnahme'/'Ausgabe').
    """
    query = "SELECT * FROM categories"
    params = []
    if kind:
        query += " WHERE kind = ?"
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


def category_in_use(conn, cat_id):
    """Prueft, ob eine Kategorie noch verwendet wird - sowohl direkt an
    einer Buchung als auch an einem einzelnen Posten innerhalb einer
    aufgeteilten Buchung."""
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM transactions WHERE category_id = ?) +
            (SELECT COUNT(*) FROM transaction_items WHERE category_id = ?) AS c
        """,
        (cat_id, cat_id),
    ).fetchone()
    return row["c"] > 0


def category_has_children(conn, cat_id):
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM categories WHERE parent_id = ?", (cat_id,)
    ).fetchone()
    return row["c"] > 0


# ----------------------------------------------------- Buchungsposten

def get_items(conn, transaction_id):
    return conn.execute(
        """
        SELECT i.*,
               COALESCE(CASE WHEN p.name IS NOT NULL THEN p.name || ' \u203a ' || c.name ELSE c.name END, NULL) AS category_name
        FROM transaction_items i
        LEFT JOIN categories c ON c.id = i.category_id
        LEFT JOIN categories p ON p.id = c.parent_id
        WHERE i.transaction_id = ?
        ORDER BY i.position, i.id
        """,
        (transaction_id,),
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

def monthly_summary(conn, start_date):
    """Einnahmen/Ausgaben je Kalendermonat ab start_date (YYYY-MM-DD).

    Rueckgabe: dict {"YYYY-MM": {"einnahmen": x, "ausgaben": y}}
    """
    rows = conn.execute(
        """
        SELECT strftime('%Y-%m', date) AS ym,
               COALESCE(SUM(CASE WHEN type = 'Einnahme' THEN amount END), 0) AS einnahmen,
               COALESCE(SUM(CASE WHEN type = 'Ausgabe' THEN amount END), 0) AS ausgaben
        FROM transactions
        WHERE date >= ? AND deleted = 0
        GROUP BY ym
        """,
        (start_date,),
    ).fetchall()
    return {r["ym"]: {"einnahmen": r["einnahmen"], "ausgaben": r["ausgaben"]} for r in rows}


def category_breakdown(conn, tx_type, start_date):
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
        WHERE flat.type = ? AND flat.date >= ? AND flat.deleted = 0
        GROUP BY category
        ORDER BY summe DESC
        """,
        (tx_type, start_date),
    ).fetchall()
    return rows


def earliest_transaction_date(conn):
    row = conn.execute("SELECT MIN(date) AS d FROM transactions WHERE deleted = 0").fetchone()
    return row["d"]


def net_worth_series(conn):
    """Kumulierter Gesamtbestand ueber die Zeit (nur aktive Konten).

    Umbuchungen wirken sich per Definition nicht auf die Summe aus.
    Rueckgabe: (start_total, [{"date": ..., "total": ...}, ...])
    """
    start_total = conn.execute(
        "SELECT COALESCE(SUM(initial_balance), 0) AS s FROM accounts WHERE archived = 0"
    ).fetchone()["s"]

    rows = conn.execute(
        """
        SELECT t.date AS date,
               SUM(CASE WHEN t.type = 'Einnahme' THEN t.amount
                        WHEN t.type = 'Ausgabe' THEN -t.amount
                        ELSE 0 END) AS net
        FROM transactions t
        JOIN accounts a ON a.id = t.account_id
        WHERE a.archived = 0 AND t.deleted = 0
        GROUP BY t.date
        ORDER BY t.date
        """
    ).fetchall()

    series = []
    running = start_total
    for r in rows:
        running += r["net"]
        series.append({"date": r["date"], "total": running})
    return start_total, series
