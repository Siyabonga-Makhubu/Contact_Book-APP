import base64
import binascii
import json
import mimetypes
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("CONTACT_BOOK_DB", ROOT / "contact_book.sqlite3"))
UPLOADS = Path(os.environ.get("CONTACT_BOOK_UPLOADS", ROOT / "uploads"))
MAX_BODY_SIZE = 8 * 1024 * 1024
MAX_IMAGE_SIZE = 5 * 1024 * 1024
IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
CONTACT_FIELDS = ("firstname", "lastname", "email", "phone", "address")


def connect_database():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


@contextmanager
def database():
    connection = connect_database()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    with database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                firstname TEXT NOT NULL,
                lastname TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                phone TEXT NOT NULL DEFAULT '',
                address TEXT NOT NULL DEFAULT '',
                avatar TEXT NOT NULL DEFAULT ''
            )
            """
        )


def contact_to_dict(row):
    return dict(row)


class ContactBookHandler(BaseHTTPRequestHandler):
    def send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length.") from error
        if length < 0 or length > MAX_BODY_SIZE:
            raise ValueError("Request body is too large.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("Request body must be valid JSON.") from error
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def normalize_contact(self, payload, current=None):
        contact = {}
        for field in CONTACT_FIELDS:
            value = payload.get(field, current[field] if current else "")
            if not isinstance(value, str):
                raise ValueError(f"{field} must be text.")
            value = value.strip()
            if len(value) > 500:
                raise ValueError(f"{field} must be 500 characters or fewer.")
            contact[field] = value
        if not contact["firstname"] or not contact["lastname"]:
            raise ValueError("First name and last name are required.")
        if contact["email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact["email"]):
            raise ValueError("Enter a valid email address.")
        return contact

    def save_avatar(self, value):
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("avatar must be an image data URL.")
        match = re.fullmatch(r"data:(image/(?:jpeg|png|gif|webp));base64,([A-Za-z0-9+/]*={0,2})", value)
        if not match:
            raise ValueError("Avatar must be a JPEG, PNG, GIF, or WebP image.")
        try:
            contents = base64.b64decode(match.group(2), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("Avatar image data is invalid.") from error
        if not contents or len(contents) > MAX_IMAGE_SIZE:
            raise ValueError("Avatar must be between 1 byte and 5 MB.")
        filename = f"{uuid.uuid4().hex}{IMAGE_TYPES[match.group(1)]}"
        UPLOADS.mkdir(parents=True, exist_ok=True)
        (UPLOADS / filename).write_bytes(contents)
        return filename

    def remove_avatar(self, filename):
        if filename:
            try:
                (UPLOADS / Path(filename).name).unlink(missing_ok=True)
            except OSError:
                self.log_error("Could not remove avatar %s", filename)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        if path == "/api/contacts":
            try:
                with database() as connection:
                    rows = connection.execute(
                        "SELECT id, firstname, lastname, email, phone, address, avatar FROM contacts ORDER BY lastname, firstname, id"
                    ).fetchall()
            except sqlite3.Error as error:
                self.log_error("Database error: %s", error)
                self.send_json(500, {"error": "Could not load contacts."})
                return
            self.send_json(200, [contact_to_dict(row) for row in rows])
            return
        match = re.fullmatch(r"/api/contacts/([0-9]+)", path)
        if match:
            try:
                with database() as connection:
                    row = connection.execute(
                        "SELECT id, firstname, lastname, email, phone, address, avatar FROM contacts WHERE id = ?",
                        (int(match.group(1)),),
                    ).fetchone()
            except sqlite3.Error as error:
                self.log_error("Database error: %s", error)
                self.send_json(500, {"error": "Could not load contact."})
                return
            if row is None:
                self.send_json(404, {"error": "Contact not found."})
                return
            self.send_json(200, contact_to_dict(row))
            return
        if path.startswith("/uploads/"):
            filename = unquote(path[len("/uploads/"):])
            if not re.fullmatch(r"[a-f0-9]{32}\.(?:jpg|png|gif|webp)", filename):
                self.send_json(404, {"error": "Image not found."})
                return
            image_path = UPLOADS / filename
            try:
                contents = image_path.read_bytes()
            except FileNotFoundError:
                self.send_json(404, {"error": "Image not found."})
                return
            except OSError as error:
                self.log_error("Could not read avatar: %s", error)
                self.send_json(500, {"error": "Could not load image."})
                return
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(filename)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(contents)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(contents)
            return
        self.send_json(404, {"error": "Not found."})

    def do_POST(self):
        if urlsplit(self.path).path != "/api/contacts":
            self.send_json(404, {"error": "Not found."})
            return
        new_avatar = None
        try:
            payload = self.read_json()
            contact = self.normalize_contact(payload)
            new_avatar = self.save_avatar(payload.get("avatar"))
            contact["avatar"] = new_avatar or ""
            with database() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO contacts (firstname, lastname, email, phone, address, avatar)
                    VALUES (:firstname, :lastname, :email, :phone, :address, :avatar)
                    """,
                    contact,
                )
                contact["id"] = cursor.lastrowid
            self.send_json(201, contact)
        except ValueError as error:
            if new_avatar:
                self.remove_avatar(new_avatar)
            self.send_json(400, {"error": str(error)})
        except sqlite3.Error as error:
            if new_avatar:
                self.remove_avatar(new_avatar)
            self.log_error("Database error: %s", error)
            self.send_json(500, {"error": "Could not save contact."})

    def do_PUT(self):
        match = re.fullmatch(r"/api/contacts/([0-9]+)", urlsplit(self.path).path)
        if not match:
            self.send_json(404, {"error": "Not found."})
            return
        contact_id = int(match.group(1))
        new_avatar = None
        try:
            payload = self.read_json()
            with database() as connection:
                current = connection.execute(
                    "SELECT id, firstname, lastname, email, phone, address, avatar FROM contacts WHERE id = ?",
                    (contact_id,),
                ).fetchone()
                if current is None:
                    self.send_json(404, {"error": "Contact not found."})
                    return
                contact = self.normalize_contact(payload, current)
                new_avatar = self.save_avatar(payload.get("avatar"))
                contact["avatar"] = new_avatar or current["avatar"]
                contact["id"] = contact_id
                connection.execute(
                    """
                    UPDATE contacts
                    SET firstname = :firstname, lastname = :lastname, email = :email,
                        phone = :phone, address = :address, avatar = :avatar
                    WHERE id = :id
                    """,
                    contact,
                )
            if new_avatar:
                self.remove_avatar(current["avatar"])
            self.send_json(200, contact)
        except ValueError as error:
            if new_avatar:
                self.remove_avatar(new_avatar)
            self.send_json(400, {"error": str(error)})
        except sqlite3.Error as error:
            if new_avatar:
                self.remove_avatar(new_avatar)
            self.log_error("Database error: %s", error)
            self.send_json(500, {"error": "Could not update contact."})

    def do_DELETE(self):
        match = re.fullmatch(r"/api/contacts/([0-9]+)", urlsplit(self.path).path)
        if not match:
            self.send_json(404, {"error": "Not found."})
            return
        try:
            with database() as connection:
                row = connection.execute(
                    "SELECT avatar FROM contacts WHERE id = ?", (int(match.group(1)),)
                ).fetchone()
                if row is None:
                    self.send_json(404, {"error": "Contact not found."})
                    return
                connection.execute("DELETE FROM contacts WHERE id = ?", (int(match.group(1)),))
        except sqlite3.Error as error:
            self.log_error("Database error: %s", error)
            self.send_json(500, {"error": "Could not delete contact."})
            return
        self.remove_avatar(row["avatar"])
        self.send_json(200, {"message": "Contact deleted."})

    def log_message(self, format_string, *args):
        super().log_message(format_string, *args)


def main():
    initialize_database()
    port = int(os.environ.get("CONTACT_BOOK_API_PORT", "8001"))
    server = ThreadingHTTPServer(("127.0.0.1", port), ContactBookHandler)
    print(f"Contact API listening on http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
