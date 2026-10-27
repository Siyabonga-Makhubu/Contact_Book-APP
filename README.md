# Contact Book

The app uses a local Python API with SQLite storage. Node.js serves the browser app and starts the API.

## Run locally

1. Install Node.js and Python 3.
2. From this folder, run `npm start`.
3. Open `http://localhost:8080`.

The API listens on `127.0.0.1:8001`. Contact data is saved in `contact_book.sqlite3`, and profile images are stored in `uploads/`. Both are generated locally and excluded from Git. The Python API uses only the standard library, so no Python package installation is needed.

Set `PORT` or `CONTACT_BOOK_API_PORT` to change the web or API port. Set `PYTHON` if your Python executable is not named `python`.
