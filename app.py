# =============================================================
#  MedQueue — Backend Python (Flask)
#  Remplace la logique de supabase.js côté frontend
#  Toutes les requêtes Supabase passent par ce serveur Python
#  Run : python app.py
#  Dépendances : pip install flask flask-cors supabase python-dotenv
# =============================================================

from flask import Flask, jsonify, request
from flask_cors import CORS
from supabase import create_client, Client
from datetime import date
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # Autorise les requêtes depuis React Native / Expo

# ── Connexion Supabase ─────────────────────────────────────────
SUPABASE_URL      = os.getenv("SUPABASE_URL",      "https://hgbgxuqwabzuzwxndymz.supabase.co")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "sb_publishable_nCf7BaDbwxMRHGhwuysx-g_rlEvZ83z")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_ANON_KEY)


def today_str():
    return date.today().isoformat()


# =============================================================
#  MÉDECINS
# =============================================================

@app.route("/medecins", methods=["GET"])
def get_medecins():
    """
    GET /medecins?ville=Marrakech&specialite=Cardiologue
    Retourne la liste des médecins avec leur file du jour.
    """
    ville      = request.args.get("ville", "Marrakech")
    specialite = request.args.get("specialite", None)

    query = (
        supabase.table("medecins")
        .select("id, nom, specialite, ville, email, files_attente(id, date, statut, ticket_actuel, ticket_suivant)")
        .eq("ville", ville)
        .eq("files_attente.date", today_str())
        .order("nom")
    )

    if specialite:
        query = query.eq("specialite", specialite)

    response = query.execute()
    return jsonify(response.data), 200


# =============================================================
#  FILES D'ATTENTE
# =============================================================

@app.route("/files/<int:medecin_id>", methods=["GET"])
def get_ou_creer_file(medecin_id):
    """
    GET /files/<medecin_id>
    Récupère ou crée la file du jour pour un médecin.
    """
    today = today_str()

    # Chercher une file existante
    existing = (
        supabase.table("files_attente")
        .select("*")
        .eq("medecin_id", medecin_id)
        .eq("date", today)
        .execute()
    )

    if existing.data:
        return jsonify(existing.data[0]), 200

    # Créer une nouvelle file
    created = (
        supabase.table("files_attente")
        .insert({
            "medecin_id":     medecin_id,
            "date":           today,
            "statut":         "ouverte",
            "ticket_actuel":  0,
            "ticket_suivant": 1,
        })
        .execute()
    )

    return jsonify(created.data[0]), 201


# =============================================================
#  TICKETS
# =============================================================

@app.route("/tickets/file/<int:file_id>", methods=["GET"])
def get_tickets_file(file_id):
    """
    GET /tickets/file/<file_id>
    Récupère les tickets actifs (en_attente ou appele) d'une file.
    """
    response = (
        supabase.table("tickets")
        .select("*")
        .eq("file_id", file_id)
        .in_("statut", ["en_attente", "appele"])
        .order("urgence", desc=True)
        .order("numero")
        .execute()
    )
    return jsonify(response.data), 200


@app.route("/tickets/<int:ticket_id>", methods=["GET"])
def get_ticket(ticket_id):
    """
    GET /tickets/<ticket_id>
    Récupère un ticket par son ID avec les infos de sa file.
    """
    response = (
        supabase.table("tickets")
        .select("*, files_attente(ticket_actuel, ticket_suivant, statut)")
        .eq("id", ticket_id)
        .execute()
    )
    if not response.data:
        return jsonify({"error": "Ticket introuvable"}), 404
    return jsonify(response.data[0]), 200


@app.route("/tickets", methods=["POST"])
def creer_ticket():
    """
    POST /tickets
    Body JSON : { file_id, patient_nom, patient_tel, urgence }
    Crée un nouveau ticket dans la file.
    """
    body        = request.get_json()
    file_id     = body.get("file_id")
    patient_nom = body.get("patient_nom")
    patient_tel = body.get("patient_tel")
    urgence     = body.get("urgence", False)

    if not file_id or not patient_nom:
        return jsonify({"error": "file_id et patient_nom sont requis"}), 400

    # Récupérer le numéro suivant
    file_resp = (
        supabase.table("files_attente")
        .select("ticket_suivant")
        .eq("id", file_id)
        .execute()
    )
    if not file_resp.data:
        return jsonify({"error": "File introuvable"}), 404

    numero = file_resp.data[0]["ticket_suivant"]

    # Créer le ticket
    ticket_resp = (
        supabase.table("tickets")
        .insert({
            "file_id":     file_id,
            "patient_nom": patient_nom,
            "patient_tel": patient_tel,
            "numero":      numero,
            "statut":      "en_attente",
            "urgence":     urgence,
        })
        .execute()
    )

    # Incrémenter ticket_suivant
    supabase.table("files_attente").update(
        {"ticket_suivant": numero + 1}
    ).eq("id", file_id).execute()

    return jsonify(ticket_resp.data[0]), 201


@app.route("/tickets/<int:file_id>/suivant", methods=["POST"])
def appeller_suivant(file_id):
    """
    POST /tickets/<file_id>/suivant
    Appelle le ticket suivant (urgences d'abord, puis ordre numérique).
    """
    # Récupérer le ticket actuel
    file_resp = (
        supabase.table("files_attente")
        .select("ticket_actuel")
        .eq("id", file_id)
        .execute()
    )
    if not file_resp.data:
        return jsonify({"error": "File introuvable"}), 404

    ticket_actuel = file_resp.data[0]["ticket_actuel"]

    # Terminer le ticket en cours si existant
    if ticket_actuel > 0:
        supabase.table("tickets").update(
            {"statut": "termine"}
        ).eq("file_id", file_id).eq("numero", ticket_actuel).eq("statut", "appele").execute()

    # Trouver le suivant (urgences en premier)
    suivants = (
        supabase.table("tickets")
        .select("*")
        .eq("file_id", file_id)
        .eq("statut", "en_attente")
        .order("urgence", desc=True)
        .order("numero")
        .limit(1)
        .execute()
    )

    if not suivants.data:
        return jsonify({"message": "Aucun ticket en attente", "ticket": None}), 200

    prochain = suivants.data[0]

    # Passer le ticket en "appele"
    supabase.table("tickets").update({"statut": "appele"}).eq("id", prochain["id"]).execute()
    supabase.table("files_attente").update({"ticket_actuel": prochain["numero"]}).eq("id", file_id).execute()

    return jsonify(prochain), 200


@app.route("/tickets/<int:ticket_id>/statut", methods=["PATCH"])
def update_statut_ticket(ticket_id):
    """
    PATCH /tickets/<ticket_id>/statut
    Body JSON : { statut: "en_attente" | "appele" | "termine" }
    """
    body   = request.get_json()
    statut = body.get("statut")

    if statut not in ("en_attente", "appele", "termine"):
        return jsonify({"error": "Statut invalide"}), 400

    response = (
        supabase.table("tickets")
        .update({"statut": statut})
        .eq("id", ticket_id)
        .execute()
    )
    if not response.data:
        return jsonify({"error": "Ticket introuvable"}), 404
    return jsonify(response.data[0]), 200


@app.route("/tickets/<int:ticket_id>", methods=["DELETE"])
def annuler_ticket(ticket_id):
    """
    DELETE /tickets/<ticket_id>
    Annule (supprime) un ticket en_attente.
    """
    supabase.table("tickets").delete().eq("id", ticket_id).eq("statut", "en_attente").execute()
    return jsonify({"message": "Ticket annulé"}), 200


@app.route("/tickets/position", methods=["GET"])
def get_position():
    """
    GET /tickets/position?file_id=1&numero=5
    Calcule la position d'un ticket dans sa file.
    """
    file_id   = request.args.get("file_id")
    mon_numero = int(request.args.get("numero", 0))

    response = (
        supabase.table("tickets")
        .select("numero")
        .eq("file_id", file_id)
        .eq("statut", "en_attente")
        .lt("numero", mon_numero)
        .execute()
    )
    position = len(response.data) if response.data else 0
    return jsonify({"position": position}), 200


# =============================================================
#  AUTHENTIFICATION MÉDECIN
# =============================================================

@app.route("/auth/login", methods=["POST"])
def login_medecin():
    """
    POST /auth/login
    Body JSON : { email, password }
    Authentifie un médecin via Supabase Auth.
    """
    body     = request.get_json()
    email    = body.get("email")
    password = body.get("password")

    if not email or not password:
        return jsonify({"error": "Email et mot de passe requis"}), 400

    try:
        auth_resp = supabase.auth.sign_in_with_password({"email": email, "password": password})
    except Exception:
        return jsonify({"error": "Email ou mot de passe incorrect"}), 401

    user_id = auth_resp.user.id

    # Vérifier le profil
    profile_resp = (
        supabase.table("profiles")
        .select("*")
        .eq("id", user_id)
        .execute()
    )

    if not profile_resp.data or profile_resp.data[0].get("role") != "medecin":
        supabase.auth.sign_out()
        return jsonify({"error": "Ce compte n'est pas un compte médecin"}), 403

    # Récupérer les infos du médecin
    medecin_resp = (
        supabase.table("medecins")
        .select("*")
        .eq("email", email)
        .execute()
    )

    result = {**profile_resp.data[0]}
    if medecin_resp.data:
        result.update(medecin_resp.data[0])

    return jsonify(result), 200


@app.route("/auth/logout", methods=["POST"])
def logout_medecin():
    """
    POST /auth/logout
    Déconnecte le médecin.
    """
    supabase.auth.sign_out()
    return jsonify({"message": "Déconnecté"}), 200


# =============================================================
#  POINT DE SANTÉ
# =============================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "backend": "Python Flask", "version": "1.0.0"}), 200


# =============================================================
#  LANCEMENT
# =============================================================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
