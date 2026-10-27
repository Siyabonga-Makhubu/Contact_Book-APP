import sqlite3
from flask import Flask, request
 
app = Flask(__name__)
 
@app.route("/user")
def get_user():
    username = request.args.get("username", "")
 
    # INTENTIONAL TEST VULNERABILITY:
    # SQL injection through string concatenation.
    connection = sqlite3.connect("test.db")
    query = "SELECT * FROM users WHERE username = '" + username + "'"
 
    result = connection.execute(query).fetchall()
    connection.close()
 
    return {"users": result}