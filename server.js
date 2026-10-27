const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");

const root = __dirname;
const webPort = Number(process.env.PORT || 8080);
const apiPort = Number(process.env.CONTACT_BOOK_API_PORT || 8001);
const pythonCommand = process.env.PYTHON || "python";
let shuttingDown = false;
const python = spawn(pythonCommand, ["server.py"], {
  cwd: root,
  env: { ...process.env, CONTACT_BOOK_API_PORT: String(apiPort) },
  stdio: "inherit",
});

python.on("error", (error) => {
  console.error(`Could not start Python (${pythonCommand}): ${error.message}`);
  shutdown();
  process.exitCode = 1;
});

python.on("exit", (code) => {
  if (!shuttingDown) {
    console.error(`Python API stopped unexpectedly (exit code ${code}).`);
    process.exitCode = code || 1;
    server.close();
  }
});

const mimeTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
};

const server = http.createServer((request, response) => {
  const requestPath = new URL(request.url, `http://${request.headers.host}`).pathname;
  if (requestPath.startsWith("/api/") || requestPath.startsWith("/uploads/")) {
    const proxyRequest = http.request(
      {
        hostname: "127.0.0.1",
        port: apiPort,
        path: request.url,
        method: request.method,
        headers: request.headers,
      },
      (proxyResponse) => {
        response.writeHead(proxyResponse.statusCode, proxyResponse.headers);
        proxyResponse.pipe(response);
      }
    );
    proxyRequest.on("error", () => {
      if (!response.headersSent) {
        response.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
      }
      response.end(JSON.stringify({ error: "The Python contact API is unavailable." }));
    });
    request.pipe(proxyRequest);
    return;
  }

  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end("Method not allowed");
    return;
  }

  let decodedPath;
  try {
    decodedPath = decodeURIComponent(requestPath);
  } catch {
    response.writeHead(400);
    response.end("Bad request");
    return;
  }
  const relativePath = decodedPath === "/" ? "index.html" : decodedPath.slice(1);
  const filePath = path.resolve(root, relativePath);
  if (!filePath.startsWith(`${root}${path.sep}`) || !mimeTypes[path.extname(filePath)]) {
    response.writeHead(404);
    response.end("Not found");
    return;
  }
  fs.readFile(filePath, (error, contents) => {
    if (error) {
      response.writeHead(error.code === "ENOENT" ? 404 : 500);
      response.end(error.code === "ENOENT" ? "Not found" : "Could not read file");
      return;
    }
    response.writeHead(200, {
      "Content-Type": mimeTypes[path.extname(filePath)],
      "X-Content-Type-Options": "nosniff",
    });
    response.end(contents);
  });
});

server.on("error", (error) => {
  console.error(`Could not start the web server: ${error.message}`);
  process.exitCode = 1;
  shutdown();
});

server.listen(webPort, "127.0.0.1", () => {
  console.log(`Contact Book running at http://localhost:${webPort}`);
});

function shutdown() {
  shuttingDown = true;
  if (server.listening) server.close();
  python.kill();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
