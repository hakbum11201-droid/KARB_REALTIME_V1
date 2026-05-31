from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import os
import argparse

WEB_DIR = os.path.join(os.path.dirname(__file__), '..', 'web')
RUNTIME_DIR = os.path.join(os.path.dirname(__file__), '..', 'runtime')

class KarbHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        if self.path == '/api/data':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            data = {}
            try:
                with open(os.path.join(RUNTIME_DIR, 'state.json'), 'r') as f:
                    data['state'] = json.load(f)
            except Exception:
                data['state'] = {}
                
            try:
                with open(os.path.join(RUNTIME_DIR, 'latest_quotes.json'), 'r') as f:
                    data['quotes'] = json.load(f)
            except Exception:
                data['quotes'] = {}
                
            self.wfile.write(json.dumps(data).encode('utf-8'))
        else:
            super().do_GET()

def run(server_class=HTTPServer, handler_class=KarbHandler, port=8000, once=False):
    server_address = ('', port)
    httpd = server_class(server_address, handler_class)
    print(f"Starting lightweight UI server on port {port}...")
    if once:
        httpd.handle_request()
    else:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        httpd.server_close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    run(once=args.once)
