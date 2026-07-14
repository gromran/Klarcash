"""Ausgabenverwaltung - Flask-Webanwendung.

Start: python app.py
Anschliessend im Browser http://127.0.0.1:5000 oeffnen.

Im lokalen Netz erreichbar (z. B. fuers Handy): python app.py --online
Achtung: Die App hat keine Anmeldung - nur in vertrauenswuerdigen Netzen nutzen.
"""

from datetime import date, datetime
from calendar import month_name
import argparse
import csv
import io
import socket

from flask import Flask, render_template, request, redirect, url_for, flash, Response, jsonify

import db

app = Flask(__name__)
app.secret_key = "dev-only-change-me"  # nur fuer Flash-Messages, keine echten Sessions

MONTHS_DE = [
    "", "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


@app.template_filter("eur")
def format_eur(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value
    return f"{value:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


@app.template_filter("de_date")
def format_de_date(value):
    try:
        d = datetime.strptime(value, "%Y-%m-%d")
        return d.strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return value


# ---------------------------------------------------------------- Dashboard

@app.route("/")
def dashboard():
    with db.db_session() as conn:
        accounts = db.accounts_with_balances(conn)
        gesamt = sum(a["balance"] for a in accounts)

        recent = conn.execute(
            """
            SELECT t.*, a.name AS account_name,
                   CASE WHEN p.name IS NOT NULL THEN p.name || ' › ' || c.name ELSE c.name END AS category_name,
                   ta.name AS target_account_name,
                   (SELECT COUNT(*) FROM transaction_items i WHERE i.transaction_id = t.id) AS item_count
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            LEFT JOIN categories p ON p.id = c.parent_id
            LEFT JOIN accounts ta ON ta.id = t.target_account_id
            WHERE t.deleted = 0
            ORDER BY t.date DESC, t.id DESC
            LIMIT 10
            """
        ).fetchall()

        today = date.today()
        month_start = today.replace(day=1).isoformat()
        month_row = conn.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN type = 'Einnahme' THEN amount END), 0) AS einnahmen,
                COALESCE(SUM(CASE WHEN type = 'Ausgabe' THEN amount END), 0) AS ausgaben
            FROM transactions
            WHERE date >= ? AND deleted = 0
            """,
            (month_start,),
        ).fetchone()

    return render_template(
        "dashboard.html",
        accounts=accounts,
        gesamt=gesamt,
        recent=recent,
        monat_name=MONTHS_DE[today.month],
        monat_einnahmen=month_row["einnahmen"],
        monat_ausgaben=month_row["ausgaben"],
        monat_saldo=month_row["einnahmen"] - month_row["ausgaben"],
    )


# ------------------------------------------------------------- Buchungen

@app.route("/buchungen")
def transactions_list():
    account_filter = request.args.get("konto", type=int)
    type_filter = request.args.get("typ", default="")
    date_from = request.args.get("von", default="")
    date_to = request.args.get("bis", default="")
    search = request.args.get("suche", default="").strip()
    page = request.args.get("seite", default=1, type=int)
    if page < 1:
        page = 1

    where = "WHERE t.deleted = 0"
    params = []
    if account_filter:
        where += " AND (t.account_id = ? OR t.target_account_id = ?)"
        params += [account_filter, account_filter]
    if type_filter:
        where += " AND t.type = ?"
        params.append(type_filter)
    if date_from:
        where += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        where += " AND t.date <= ?"
        params.append(date_to)
    if search:
        where += " AND t.description LIKE ?"
        params.append(f"%{search}%")

    with db.db_session() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS c FROM transactions t {where}", params).fetchone()["c"]
        total_pages = max(1, -(-total // db.PAGE_SIZE))  # aufgerundete Division
        page = min(page, total_pages)
        offset = (page - 1) * db.PAGE_SIZE

        rows = conn.execute(
            f"""
            SELECT t.*, a.name AS account_name,
                   CASE WHEN p.name IS NOT NULL THEN p.name || ' › ' || c.name ELSE c.name END AS category_name,
                   ta.name AS target_account_name,
                   (SELECT COUNT(*) FROM transaction_items i WHERE i.transaction_id = t.id) AS item_count
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            LEFT JOIN categories p ON p.id = c.parent_id
            LEFT JOIN accounts ta ON ta.id = t.target_account_id
            {where}
            ORDER BY t.date DESC, t.id DESC
            LIMIT ? OFFSET ?
            """,
            params + [db.PAGE_SIZE, offset],
        ).fetchall()

        accounts = db.accounts_with_balances(conn, include_archived=True)

        sums = conn.execute(
            f"""
            SELECT COALESCE(SUM(CASE WHEN t.type = 'Einnahme' THEN t.amount END), 0) AS ein,
                   COALESCE(SUM(CASE WHEN t.type = 'Ausgabe' THEN t.amount END), 0) AS aus
            FROM transactions t {where}
            """,
            params,
        ).fetchone()

    return render_template(
        "transactions_list.html",
        rows=rows,
        accounts=accounts,
        summe_einnahmen=sums["ein"],
        summe_ausgaben=sums["aus"],
        f_konto=account_filter or "",
        f_typ=type_filter,
        f_von=date_from,
        f_bis=date_to,
        f_suche=search,
        page=page,
        total_pages=total_pages,
        total=total,
    )


def _validate_transaction_form(form, items_total=None):
    """Liest und validiert die Felder eines Buchungsformulars.

    items_total: falls die Buchung ueber Posten (Kassenzettel) erfasst wird,
    wird hier die bereits berechnete Postensumme uebergeben - sie ersetzt
    dann den Wert des "amount"-Feldes (das UI haelt es zwar per JS synchron,
    serverseitig verlassen wir uns aber nicht darauf).

    Gibt (data_dict, errors_list) zurueck. data_dict ist auch bei Fehlern
    vollstaendig befuellt (mit None fuer nicht parsebare Werte), damit der
    Aufrufer bei Bedarf trotzdem darauf zugreifen kann.
    """
    tx_type = form.get("type")
    account_id = form.get("account_id", type=int)
    tx_date = form.get("date") or date.today().isoformat()
    description = form.get("description", "").strip()
    category_id = form.get("category_id", type=int) or None
    target_account_id = form.get("target_account_id", type=int) or None

    errors = []
    if items_total is not None:
        amount = round(items_total, 2)
        if amount <= 0:
            errors.append("Die Summe der Posten muss größer als 0 sein.")
    else:
        amount_raw = form.get("amount", "").replace(",", ".").strip()
        try:
            amount = float(amount_raw)
            if amount <= 0:
                errors.append("Der Betrag muss größer als 0 sein.")
        except ValueError:
            errors.append("Bitte einen gültigen Betrag angeben.")
            amount = None

    if tx_type not in ("Einnahme", "Ausgabe", "Umbuchung"):
        errors.append("Bitte eine gültige Buchungsart wählen.")
    if not account_id:
        errors.append("Bitte ein Konto auswählen.")
    if tx_type == "Umbuchung":
        if not target_account_id:
            errors.append("Bitte ein Zielkonto für die Umbuchung auswählen.")
        elif target_account_id == account_id:
            errors.append("Quell- und Zielkonto dürfen nicht identisch sein.")
        category_id = None
    else:
        target_account_id = None

    data = {
        "date": tx_date,
        "account_id": account_id,
        "type": tx_type,
        "category_id": category_id,
        "description": description,
        "amount": amount,
        "target_account_id": target_account_id,
    }
    return data, errors


def _parse_items(form):
    """Liest die Kassenzettel-Posten aus dem Formular (parallele Listen
    item_description / item_category_id / item_amount). Leere Zeilen
    (weder Beschreibung noch Betrag) werden ignoriert.

    Gibt (items, errors) zurueck. items ist eine Liste von dicts mit
    description/amount/category_id.
    """
    descriptions = form.getlist("item_description")
    category_ids = form.getlist("item_category_id")
    amounts = form.getlist("item_amount")

    items = []
    errors = []
    for i in range(len(amounts)):
        desc = descriptions[i].strip() if i < len(descriptions) else ""
        cat_raw = category_ids[i] if i < len(category_ids) else ""
        amt_raw = amounts[i].replace(",", ".").strip() if i < len(amounts) else ""

        if not desc and not amt_raw:
            continue  # vollstaendig leere Zeile ignorieren

        try:
            amt = float(amt_raw)
        except ValueError:
            errors.append(f"Posten {i + 1}: Bitte einen gültigen Betrag angeben.")
            continue
        if amt == 0:
            errors.append(f"Posten {i + 1}: Der Betrag darf nicht 0 sein.")
            continue

        cat_id = int(cat_raw) if cat_raw else None
        items.append({"description": desc, "amount": amt, "category_id": cat_id})

    return items, errors


@app.route("/buchungen/neu", methods=["GET", "POST"])
def transaction_new():
    with db.db_session() as conn:
        accounts = db.accounts_with_balances(conn, include_archived=True)
        if not accounts:
            flash("Bitte lege zuerst ein Konto an, bevor du Buchungen erfassen kannst.", "error")
            return redirect(url_for("account_new"))

        categories = db.category_tree(conn)

        if request.method == "POST":
            use_items = (
                request.form.get("use_items") == "on"
                and request.form.get("type") in ("Einnahme", "Ausgabe")
            )
            items, item_errors = [], []
            items_total = None
            if use_items:
                items, item_errors = _parse_items(request.form)
                if not items:
                    item_errors.append("Bitte mindestens einen Posten angeben oder die Aufteilung deaktivieren.")
                else:
                    items_total = sum(i["amount"] for i in items)

            data, errors = _validate_transaction_form(request.form, items_total=items_total)
            errors += item_errors

            if errors:
                for e in errors:
                    flash(e, "error")
            else:
                if use_items and items:
                    data["category_id"] = None
                cur = conn.execute(
                    """
                    INSERT INTO transactions
                        (date, account_id, type, category_id, description, amount, target_account_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (data["date"], data["account_id"], data["type"], data["category_id"],
                     data["description"], data["amount"], data["target_account_id"]),
                )
                if use_items and items:
                    db.replace_items(
                        conn, cur.lastrowid,
                        [(i["description"], i["amount"], i["category_id"]) for i in items],
                    )
                flash("Buchung gespeichert.", "success")
                return redirect(url_for("dashboard"))

    return render_template(
        "transaction_form.html",
        accounts=accounts,
        categories=categories,
        today=date.today().isoformat(),
        form=request.form,
        tx=None,
        items=[],
    )


@app.route("/buchungen/<int:tx_id>/bearbeiten", methods=["GET", "POST"])
def transaction_edit(tx_id):
    with db.db_session() as conn:
        tx = conn.execute(
            "SELECT * FROM transactions WHERE id = ? AND deleted = 0", (tx_id,)
        ).fetchone()
        if not tx:
            flash("Buchung wurde nicht gefunden.", "error")
            return redirect(url_for("transactions_list"))

        accounts = db.accounts_with_balances(conn, include_archived=True)
        categories = db.category_tree(conn)

        if request.method == "POST":
            use_items = (
                request.form.get("use_items") == "on"
                and request.form.get("type") in ("Einnahme", "Ausgabe")
            )
            items, item_errors = [], []
            items_total = None
            if use_items:
                items, item_errors = _parse_items(request.form)
                if not items:
                    item_errors.append("Bitte mindestens einen Posten angeben oder die Aufteilung deaktivieren.")
                else:
                    items_total = sum(i["amount"] for i in items)

            data, errors = _validate_transaction_form(request.form, items_total=items_total)
            errors += item_errors

            if errors:
                for e in errors:
                    flash(e, "error")
                form_values = request.form
                items_for_template = [
                    {"description": i["description"], "amount": i["amount"], "category_id": i["category_id"]}
                    for i in items
                ] if use_items else []
            else:
                if use_items and items:
                    data["category_id"] = None
                conn.execute(
                    """
                    UPDATE transactions
                    SET date = ?, account_id = ?, type = ?, category_id = ?,
                        description = ?, amount = ?, target_account_id = ?
                    WHERE id = ?
                    """,
                    (data["date"], data["account_id"], data["type"], data["category_id"],
                     data["description"], data["amount"], data["target_account_id"], tx_id),
                )
                db.replace_items(
                    conn, tx_id,
                    [(i["description"], i["amount"], i["category_id"]) for i in items]
                    if (use_items and items) else [],
                )
                flash("Buchung aktualisiert.", "success")
                return redirect(url_for("transactions_list"))
        else:
            form_values = dict(tx)
            items_for_template = db.get_items(conn, tx_id)

    return render_template(
        "transaction_form.html",
        accounts=accounts,
        categories=categories,
        today=date.today().isoformat(),
        form=form_values,
        tx=tx,
        items=items_for_template,
    )


@app.route("/buchungen/<int:tx_id>/loeschen", methods=["POST"])
def transaction_delete(tx_id):
    with db.db_session() as conn:
        conn.execute(
            "UPDATE transactions SET deleted = 1, deleted_at = datetime('now') WHERE id = ?",
            (tx_id,),
        )
    flash("Buchung in den Papierkorb verschoben.", "success")
    return redirect(request.referrer or url_for("transactions_list"))


@app.route("/papierkorb")
def trash():
    with db.db_session() as conn:
        rows = conn.execute(
            """
            SELECT t.*, a.name AS account_name,
                   CASE WHEN p.name IS NOT NULL THEN p.name || ' › ' || c.name ELSE c.name END AS category_name,
                   ta.name AS target_account_name
            FROM transactions t
            JOIN accounts a ON a.id = t.account_id
            LEFT JOIN categories c ON c.id = t.category_id
            LEFT JOIN categories p ON p.id = c.parent_id
            LEFT JOIN accounts ta ON ta.id = t.target_account_id
            WHERE t.deleted = 1
            ORDER BY t.deleted_at DESC
            """
        ).fetchall()
    return render_template("trash.html", rows=rows)


@app.route("/papierkorb/<int:tx_id>/wiederherstellen", methods=["POST"])
def transaction_restore(tx_id):
    with db.db_session() as conn:
        conn.execute(
            "UPDATE transactions SET deleted = 0, deleted_at = NULL WHERE id = ?", (tx_id,)
        )
    flash("Buchung wiederhergestellt.", "success")
    return redirect(url_for("trash"))


@app.route("/papierkorb/<int:tx_id>/endgueltig-loeschen", methods=["POST"])
def transaction_purge(tx_id):
    with db.db_session() as conn:
        db.delete_items(conn, tx_id)
        conn.execute("DELETE FROM transactions WHERE id = ? AND deleted = 1", (tx_id,))
    flash("Buchung endgültig gelöscht.", "success")
    return redirect(url_for("trash"))


# --------------------------------------------------------------- Konten

@app.route("/konten")
def accounts_overview():
    with db.db_session() as conn:
        accounts = db.accounts_with_balances(conn, include_archived=True)
    return render_template("accounts.html", accounts=accounts)


@app.route("/konten/neu", methods=["GET", "POST"])
def account_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        acc_type = request.form.get("type")
        initial = request.form.get("initial_balance", "0").replace(",", ".").strip() or "0"

        errors = []
        if not name:
            errors.append("Bitte einen Kontonamen angeben.")
        if acc_type not in ("Konto", "Bar", "Anlage"):
            errors.append("Bitte einen gültigen Kontotyp wählen.")
        try:
            initial_balance = float(initial)
        except ValueError:
            errors.append("Der Anfangsbestand muss eine Zahl sein.")
            initial_balance = 0

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            with db.db_session() as conn:
                conn.execute(
                    "INSERT INTO accounts (name, type, initial_balance) VALUES (?, ?, ?)",
                    (name, acc_type, initial_balance),
                )
            flash("Konto angelegt.", "success")
            return redirect(url_for("accounts_overview"))

    return render_template("account_form.html", form=request.form)


@app.route("/konten/<int:acc_id>/archivieren", methods=["POST"])
def account_archive(acc_id):
    with db.db_session() as conn:
        current = conn.execute("SELECT archived FROM accounts WHERE id = ?", (acc_id,)).fetchone()
        new_state = 0 if current and current["archived"] else 1
        conn.execute("UPDATE accounts SET archived = ? WHERE id = ?", (new_state, acc_id))
    flash("Konto aktualisiert.", "success")
    return redirect(url_for("accounts_overview"))


# ----------------------------------------------------------- Kategorien

def _validate_category_form(conn, name, kind, parent_id, current_id=None):
    """Prueft Name/Art/Elternkategorie. current_id wird beim Bearbeiten
    mitgegeben, damit eine Kategorie sich nicht selbst als Eltern waehlen
    kann und Kategorien mit Unterkategorien nicht selbst zur Unterkategorie
    werden."""
    errors = []
    if not name or kind not in ("Einnahme", "Ausgabe"):
        errors.append("Bitte Name und Art der Kategorie angeben.")
        return errors

    if parent_id:
        if current_id and parent_id == current_id:
            errors.append("Eine Kategorie kann nicht ihre eigene übergeordnete Kategorie sein.")
            return errors
        parent = conn.execute("SELECT * FROM categories WHERE id = ?", (parent_id,)).fetchone()
        if not parent:
            errors.append("Übergeordnete Kategorie wurde nicht gefunden.")
        elif parent["parent_id"] is not None:
            errors.append("Es sind nur zwei Ebenen möglich – bitte eine Hauptkategorie als übergeordnete Kategorie wählen.")
        elif parent["kind"] != kind:
            errors.append("Die Art muss mit der übergeordneten Kategorie übereinstimmen.")
        elif current_id and db.category_has_children(conn, current_id):
            errors.append("Diese Kategorie hat noch eigene Unterkategorien und kann daher nicht selbst zu einer Unterkategorie werden.")
    return errors


@app.route("/kategorien", methods=["GET", "POST"])
def categories_view():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        kind = request.form.get("kind")
        parent_id = request.form.get("parent_id", type=int) or None

        with db.db_session() as conn:
            errors = _validate_category_form(conn, name, kind, parent_id)
            if errors:
                for e in errors:
                    flash(e, "error")
            else:
                try:
                    conn.execute(
                        "INSERT INTO categories (name, kind, parent_id) VALUES (?, ?, ?)",
                        (name, kind, parent_id),
                    )
                    flash("Kategorie angelegt.", "success")
                except Exception:
                    flash("Diese Kategorie existiert bereits.", "error")
        return redirect(url_for("categories_view"))

    with db.db_session() as conn:
        einnahmen_kategorien = db.category_tree(conn, kind="Einnahme")
        ausgaben_kategorien = db.category_tree(conn, kind="Ausgabe")
        # Hauptkategorien beider Arten fuer die "uebergeordnete Kategorie"-Auswahl
        hauptkategorien = einnahmen_kategorien + ausgaben_kategorien

    return render_template(
        "categories.html",
        einnahmen_kategorien=einnahmen_kategorien,
        ausgaben_kategorien=ausgaben_kategorien,
        hauptkategorien=hauptkategorien,
    )


@app.route("/kategorien/neu.json", methods=["POST"])
def category_create_json():
    """Legt eine Kategorie per AJAX an, ohne das Kategorien-Menue zu verlassen -
    wird vom Buchungsformular genutzt, um fehlende Kategorien inline anzulegen.
    Nutzt dieselbe Validierung wie das normale Anlegen ueber /kategorien."""
    name = request.form.get("name", "").strip()
    kind = request.form.get("kind")
    parent_id = request.form.get("parent_id", type=int) or None

    with db.db_session() as conn:
        errors = _validate_category_form(conn, name, kind, parent_id)
        if errors:
            return jsonify(ok=False, error=errors[0]), 400
        try:
            cur = conn.execute(
                "INSERT INTO categories (name, kind, parent_id) VALUES (?, ?, ?)",
                (name, kind, parent_id),
            )
            cat_id = cur.lastrowid
        except Exception:
            # name ist global UNIQUE - bei Kollision vorhandene Kategorie
            # zurueckgeben, wenn Art und Ebene passen (get-or-create).
            existing = conn.execute(
                "SELECT * FROM categories WHERE name = ?", (name,)
            ).fetchone()
            if existing and existing["kind"] == kind and existing["parent_id"] == parent_id:
                return jsonify(ok=True, category=dict(existing))
            return jsonify(ok=False, error="Diese Kategorie existiert bereits."), 400

    return jsonify(ok=True, category={
        "id": cat_id, "name": name, "kind": kind, "parent_id": parent_id,
    })


@app.route("/kategorien/<int:cat_id>/bearbeiten", methods=["GET", "POST"])
def category_edit(cat_id):
    with db.db_session() as conn:
        cat = conn.execute("SELECT * FROM categories WHERE id = ?", (cat_id,)).fetchone()
        if not cat:
            flash("Kategorie wurde nicht gefunden.", "error")
            return redirect(url_for("categories_view"))

        if request.method == "POST":
            name = request.form.get("name", "").strip()
            kind = request.form.get("kind")
            parent_id = request.form.get("parent_id", type=int) or None

            errors = _validate_category_form(conn, name, kind, parent_id, current_id=cat_id)
            if errors:
                for e in errors:
                    flash(e, "error")
                form_values = request.form
            else:
                try:
                    conn.execute(
                        "UPDATE categories SET name = ?, kind = ?, parent_id = ? WHERE id = ?",
                        (name, kind, parent_id, cat_id),
                    )
                    # Wenn sich die Art einer Hauptkategorie aendert, muessen
                    # ihre Unterkategorien mitziehen, sonst waeren sie inkonsistent.
                    conn.execute(
                        "UPDATE categories SET kind = ? WHERE parent_id = ?", (kind, cat_id)
                    )
                    flash("Kategorie aktualisiert.", "success")
                    return redirect(url_for("categories_view"))
                except Exception:
                    flash("Eine Kategorie mit diesem Namen existiert bereits.", "error")
                    form_values = request.form
        else:
            form_values = dict(cat)

        # Hauptkategorien fuer die Auswahl, die eigene Kategorie selbst ausgeschlossen
        hauptkategorien = conn.execute(
            "SELECT * FROM categories WHERE parent_id IS NULL AND id != ? ORDER BY kind, name",
            (cat_id,),
        ).fetchall()
        hat_kinder = db.category_has_children(conn, cat_id)

    return render_template(
        "category_form.html",
        cat=cat,
        form=form_values,
        hauptkategorien=hauptkategorien,
        hat_kinder=hat_kinder,
    )


@app.route("/kategorien/<int:cat_id>/loeschen", methods=["POST"])
def category_delete(cat_id):
    with db.db_session() as conn:
        if db.category_has_children(conn, cat_id):
            flash("Kategorie hat noch Unterkategorien und kann nicht gelöscht werden.", "error")
        elif db.category_in_use(conn, cat_id):
            flash("Kategorie wird noch von Buchungen verwendet und kann nicht gelöscht werden.", "error")
        else:
            conn.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
            flash("Kategorie gelöscht.", "success")
    return redirect(url_for("categories_view"))


# ------------------------------------------------------------ Statistiken

PERIODS = {
    "6": {"label": "Letzte 6 Monate", "months": 6},
    "12": {"label": "Letzte 12 Monate", "months": 12},
    "24": {"label": "Letzte 24 Monate", "months": 24},
    "alle": {"label": "Gesamter Zeitraum", "months": None},
}

CHART_PALETTE = [
    "#1F5F5B", "#B24C3A", "#8A6D3B", "#5B7B93",
    "#A6763F", "#6E7F5B", "#9C5B6E", "#4F6B5C",
]


def month_start(months_back):
    """Erster Tag des Monats, der `months_back` Monate vor heute liegt."""
    today = date.today()
    y, m = today.year, today.month - months_back
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, 1)


def month_sequence(start, end):
    """Liste von (ym-key, Label) fuer jeden Monat zwischen start und end (inklusive)."""
    seq = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        seq.append((f"{y:04d}-{m:02d}", f"{MONTHS_DE[m][:3]} {y}"))
        m += 1
        if m > 12:
            m = 1
            y += 1
    return seq


@app.route("/statistiken")
def statistics():
    period = request.args.get("zeitraum", default="12")
    if period not in PERIODS:
        period = "12"

    with db.db_session() as conn:
        if PERIODS[period]["months"] is None:
            earliest = db.earliest_transaction_date(conn)
            start = datetime.strptime(earliest, "%Y-%m-%d").date().replace(day=1) if earliest else month_start(0)
        else:
            start = month_start(PERIODS[period]["months"] - 1)
        start_str = start.isoformat()

        monthly = db.monthly_summary(conn, start_str)
        months = month_sequence(start, date.today())
        monthly_labels = [label for _, label in months]
        monthly_einnahmen = [round(monthly.get(ym, {}).get("einnahmen", 0), 2) for ym, _ in months]
        monthly_ausgaben = [round(monthly.get(ym, {}).get("ausgaben", 0), 2) for ym, _ in months]

        ausgaben_kategorien = db.category_breakdown(conn, "Ausgabe", start_str)
        einnahmen_kategorien = db.category_breakdown(conn, "Einnahme", start_str)

        summe_ausgaben = sum(r["summe"] for r in ausgaben_kategorien) or 0
        summe_einnahmen = sum(r["summe"] for r in einnahmen_kategorien) or 0

        start_total, series = db.net_worth_series(conn)
        vermoegen_labels = ["Start"] + [format_de_date(p["date"]) for p in series]
        vermoegen_werte = [round(start_total, 2)] + [round(p["total"], 2) for p in series]

    ausgaben_liste = [
        {"name": r["category"], "summe": r["summe"],
         "anteil": (r["summe"] / summe_ausgaben * 100) if summe_ausgaben else 0}
        for r in ausgaben_kategorien
    ]
    einnahmen_liste = [
        {"name": r["category"], "summe": r["summe"],
         "anteil": (r["summe"] / summe_einnahmen * 100) if summe_einnahmen else 0}
        for r in einnahmen_kategorien
    ]

    return render_template(
        "statistics.html",
        period=period,
        periods=PERIODS,
        monthly_labels=monthly_labels,
        monthly_einnahmen=monthly_einnahmen,
        monthly_ausgaben=monthly_ausgaben,
        ausgaben_liste=ausgaben_liste,
        einnahmen_liste=einnahmen_liste,
        summe_ausgaben=summe_ausgaben,
        summe_einnahmen=summe_einnahmen,
        vermoegen_labels=vermoegen_labels,
        vermoegen_werte=vermoegen_werte,
        palette=CHART_PALETTE,
    )


# -------------------------------------------------------------- Berichte

GROUP_OPTIONS = {
    "kategorie": {
        "label": "Kategorie",
        "expr": "COALESCE(CASE WHEN p.name IS NOT NULL THEN p.name || ' › ' || c.name ELSE c.name END, 'Ohne Kategorie')",
        "joins": "LEFT JOIN categories c ON c.id = t.category_id LEFT JOIN categories p ON p.id = c.parent_id",
        "use_items": True,
    },
    "hauptkategorie": {
        "label": "Hauptkategorie (Unterkategorien zusammengefasst)",
        "expr": "COALESCE(p.name, c.name, 'Ohne Kategorie')",
        "joins": "LEFT JOIN categories c ON c.id = t.category_id LEFT JOIN categories p ON p.id = c.parent_id",
        "use_items": True,
    },
    "konto": {
        "label": "Konto",
        "expr": "a.name",
        "joins": "JOIN accounts a ON a.id = t.account_id",
    },
    "monat": {
        "label": "Monat",
        "expr": "strftime('%Y-%m', t.date)",
        "joins": "",
    },
    "quartal": {
        "label": "Quartal",
        "expr": "strftime('%Y', t.date) || '-Q' || ((CAST(strftime('%m', t.date) AS INTEGER) + 2) / 3)",
        "joins": "",
    },
    "jahr": {
        "label": "Jahr",
        "expr": "strftime('%Y', t.date)",
        "joins": "",
    },
    "wochentag": {
        "label": "Wochentag",
        "expr": "strftime('%w', t.date)",
        "joins": "",
    },
    "art": {
        "label": "Buchungsart",
        "expr": "t.type",
        "joins": "",
    },
}

ARTEN_OPTIONS = {
    "ein_aus": {"label": "Einnahmen & Ausgaben", "types": ("Einnahme", "Ausgabe")},
    "einnahme": {"label": "Nur Einnahmen", "types": ("Einnahme",)},
    "ausgabe": {"label": "Nur Ausgaben", "types": ("Ausgabe",)},
    "alle": {"label": "Alle (inkl. Umbuchungen)", "types": ("Einnahme", "Ausgabe", "Umbuchung")},
}

WEEKDAY_NAMES = {
    "0": "Sonntag", "1": "Montag", "2": "Dienstag", "3": "Mittwoch",
    "4": "Donnerstag", "5": "Freitag", "6": "Samstag",
}
WEEKDAY_ORDER = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "0": 6}  # Montag zuerst


def _format_group_label(group_by, raw):
    if raw is None or raw == "":
        return "Ohne Zuordnung"
    if group_by == "monat":
        y, m = raw.split("-")
        return f"{MONTHS_DE[int(m)]} {y}"
    if group_by == "wochentag":
        return WEEKDAY_NAMES.get(raw, raw)
    return raw


def _group_sort_key(group_by, raw):
    if group_by in ("monat", "quartal", "jahr"):
        return raw  # zero-gepolsterte Strings sortieren chronologisch korrekt
    if group_by == "wochentag":
        return WEEKDAY_ORDER.get(raw, 9)
    return None  # None -> nach Betrag absteigend sortieren


def _build_report(group_by, date_from, date_to, account_id, arten):
    group_by = group_by if group_by in GROUP_OPTIONS else "kategorie"
    arten = arten if arten in ARTEN_OPTIONS else "ein_aus"
    conf = GROUP_OPTIONS[group_by]
    types = ARTEN_OPTIONS[arten]["types"]

    where = "WHERE t.deleted = 0 AND t.type IN (" + ",".join("?" for _ in types) + ")"
    params = list(types)
    if date_from:
        where += " AND t.date >= ?"
        params.append(date_from)
    if date_to:
        where += " AND t.date <= ?"
        params.append(date_to)
    if account_id:
        where += " AND (t.account_id = ? OR t.target_account_id = ?)"
        params += [account_id, account_id]

    with db.db_session() as conn:
        rows = db.grouped_report(conn, conf["expr"], conf["joins"], where, params, use_items=conf.get("use_items", False))

    results = []
    for r in rows:
        results.append({
            "label": _format_group_label(group_by, r["grp"]),
            "einnahmen": r["einnahmen"],
            "ausgaben": r["ausgaben"],
            "saldo": r["einnahmen"] - r["ausgaben"],
            "anzahl": r["anzahl"],
            "sort_key": _group_sort_key(group_by, r["grp"]),
        })

    if results and results[0]["sort_key"] is not None:
        results.sort(key=lambda x: x["sort_key"])
    else:
        results.sort(key=lambda x: x["einnahmen"] + x["ausgaben"], reverse=True)

    return group_by, arten, results


def _report_params_from_request():
    default_from = date.today().replace(month=1, day=1).isoformat()
    return {
        "group_by": request.args.get("gruppierung", default="kategorie"),
        "date_from": request.args.get("von", default=default_from),
        "date_to": request.args.get("bis", default=date.today().isoformat()),
        "account_id": request.args.get("konto", type=int),
        "arten": request.args.get("arten", default="ein_aus"),
    }


@app.route("/berichte")
def reports():
    p = _report_params_from_request()
    group_by, arten, results = _build_report(
        p["group_by"], p["date_from"], p["date_to"], p["account_id"], p["arten"]
    )

    with db.db_session() as conn:
        accounts = db.accounts_with_balances(conn, include_archived=True)

    summe_einnahmen = sum(r["einnahmen"] for r in results)
    summe_ausgaben = sum(r["ausgaben"] for r in results)
    max_wert = max([max(r["einnahmen"], r["ausgaben"]) for r in results], default=0)

    return render_template(
        "reports.html",
        results=results,
        group_options=GROUP_OPTIONS,
        arten_options=ARTEN_OPTIONS,
        group_by=group_by,
        arten=arten,
        accounts=accounts,
        f_konto=p["account_id"] or "",
        f_von=p["date_from"],
        f_bis=p["date_to"],
        summe_einnahmen=summe_einnahmen,
        summe_ausgaben=summe_ausgaben,
        max_wert=max_wert,
    )


@app.route("/berichte/export.csv")
def reports_export():
    p = _report_params_from_request()
    group_by, arten, results = _build_report(
        p["group_by"], p["date_from"], p["date_to"], p["account_id"], p["arten"]
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([GROUP_OPTIONS[group_by]["label"], "Einnahmen", "Ausgaben", "Saldo", "Anzahl Buchungen"])
    for r in results:
        writer.writerow([
            r["label"],
            f"{r['einnahmen']:.2f}".replace(".", ","),
            f"{r['ausgaben']:.2f}".replace(".", ","),
            f"{r['saldo']:.2f}".replace(".", ","),
            r["anzahl"],
        ])

    filename = f"bericht_{group_by}_{p['date_from']}_{p['date_to']}.csv"
    return Response(
        buffer.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def start(host="127.0.0.1", port=5000, db_path=None):
    """Einstiegspunkt fuer eingebettete Hosts (z. B. Android/Chaquopy).

    use_reloader=False ist zwingend: Werkzeugs Reloader startet den Prozess
    per os.execvp neu, was unter einem eingebetteten Interpreter (kein
    re-invokable sys.argv[0]) nicht funktioniert.
    """
    if db_path is not None:
        db.DB_PATH = db_path
    db.init_db()
    app.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)


def _lan_ip():
    """Ermittelt die LAN-IP dieses Rechners (ohne echte Verbindung aufzubauen)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # kein Traffic, setzt nur die Route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ausgabenverwaltung Hauptbuch")
    parser.add_argument(
        "--online",
        action="store_true",
        help="Im lokalen Netzwerk erreichbar machen (bindet an 0.0.0.0 statt 127.0.0.1).",
    )
    parser.add_argument("--port", type=int, default=5000, help="Port (Standard: 5000)")
    args = parser.parse_args()

    db.init_db()

    if args.online:
        # Kein debug=True bei externer Erreichbarkeit: der Werkzeug-Debugger
        # wuerde sonst jedem im Netz Codeausführung auf diesem Rechner erlauben.
        print(f"\n  Hauptbuch laeuft im Netzwerk:  http://{_lan_ip()}:{args.port}")
        print("  WARNUNG: Keine Anmeldung - jeder im Netzwerk kann Buchungen")
        print("           lesen und aendern. Nur in vertrauenswuerdigen Netzen nutzen.\n")
        app.run(host="0.0.0.0", port=args.port, debug=False, threaded=True)
    else:
        app.run(host="127.0.0.1", port=args.port, debug=True)
