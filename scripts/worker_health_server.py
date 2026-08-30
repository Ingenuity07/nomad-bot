import http.server
import json
import logging
import os
import sys

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s [worker_health_server]: %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


class WorkerHealthRequestHandler(http.server.BaseHTTPRequestHandler):
    """
    Extremely lightweight HTTP handler to satisfy Render web service health check probes.
    Does not load Django, Celery, or database drivers.
    """

    def do_GET(self):
        if self.path in ('/health', '/health/', '/'):
            response_body = json.dumps({
                "status": "ok",
                "service": "celery-worker-health"
            }).encode('utf-8')

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)
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

    logger.info(f"Starting Celery worker HTTP health server on {host}:{port}")
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
