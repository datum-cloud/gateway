import itertools
import json
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


connections = itertools.count(1)


class Echo(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection_id = next(connections)

    def do_GET(self):
        payload = json.dumps({
            "peer": self.client_address[0],
            "connection": self.connection_id,
            "path": self.path,
            "headers": list(self.headers.items()),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class Server(ThreadingHTTPServer):
    request_queue_size = 128


plain = Server(("0.0.0.0", 8080), Echo)
secure = Server(("0.0.0.0", 8443), Echo)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("cert.pem", "key.pem")
secure.socket = context.wrap_socket(secure.socket, server_side=True)
threading.Thread(target=plain.serve_forever, daemon=True).start()
secure.serve_forever()
