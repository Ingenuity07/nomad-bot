import http.server
import json
import logging
import os
import sys
from urllib.parse import urlparse, parse_qs

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s [worker_health_server]: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


class WorkerHealthRequestHandler(http.server.BaseHTTPRequestHandler):
    """
    Extremely lightweight HTTP handler to satisfy Render web service health check probes
    and receive wake / keep-alive ping requests.
    Does not load Django, Celery, or database drivers.
    """

    def _is_authenticated(self, query_params=None, body_json=None):
        expected_token = os.environ.get("WORKER_WAKE_TOKEN", "").strip()
        if not expected_token:
            # If no token configured in environment, allow wake (development mode)
            return True

        # Check X-Worker-Wake-Token header
        header_token = self.headers.get("X-Worker-Wake-Token", "").strip()
        if header_token and header_token == expected_token:
            return True

        # Check Authorization: Bearer <token>
        auth_header = self.headers.get("Authorization", "").strip()
        if auth_header.startswith("Bearer "):
            bearer_token = auth_header[len("Bearer "):].strip()
            if bearer_token == expected_token:
                return True

        # Check query parameters ?token=...
        if query_params and "token" in query_params:
            if query_params["token"][0].strip() == expected_token:
                return True

        # Check JSON body {"token": "..."}
        if body_json and isinstance(body_json, dict) and body_json.get("token") == expected_token:
            return True

        return False

    def _send_json_response(self, status_code, data):
        response_body = json.dumps(data).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path in ('/health', ''):
            # Render liveness health check probe
            self._send_json_response(200, {
                "status": "ok",
                "service": "celery-worker-health"
            })
        elif path == '/wake':
            # GET /wake endpoint
            query_params = parse_qs(parsed.query)
            if not self._is_authenticated(query_params=query_params):
                logger.warning("Unauthorized wake attempt on GET /wake (invalid or missing token)")
                self._send_json_response(401, {
                    "status": "unauthorized",
                    "error": "Invalid or missing wake token"
                })
                return

            logger.info("Worker received valid GET /wake keep-alive ping")
            self._send_json_response(200, {
                "status": "ok",
                "service": "celery-worker-wake",
                "message": "Worker awakened successfully"
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        if path == '/wake':
            content_length = int(self.headers.get('Content-Length', 0))
            body_json = None
            if content_length > 0:
                try:
                    body_bytes = self.rfile.read(content_length)
                    body_json = json.loads(body_bytes.decode('utf-8'))
                except Exception:
                    body_json = None

            query_params = parse_qs(parsed.query)
            if not self._is_authenticated(query_params=query_params, body_json=body_json):
                logger.warning("Unauthorized wake attempt on POST /wake (invalid or missing token)")
                self._send_json_response(401, {
                    "status": "unauthorized",
                    "error": "Invalid or missing wake token"
                })
                return

            logger.info("Worker received valid POST /wake keep-alive ping")
            self._send_json_response(200, {
                "status": "ok",
                "service": "celery-worker-wake",
                "message": "Worker awakened successfully"
            })
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Override to route HTTP access logs to standard Python logger
        logger.info("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), format % args))


def run():
    port = int(os.environ.get("PORT", 10000))
    host = "0.0.0.0"
    server_address = (host, port)

    logger.info(f"Starting Celery worker HTTP health/control server on {host}:{port}")
    try:
        httpd = http.server.HTTPServer(server_address, WorkerHealthRequestHandler)
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger.info("Health server received interrupt signal, shutting down.")
    except Exception as e:
        logger.error(f"Health server encountered fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    run()
