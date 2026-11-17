"""Loopback completion fixture: never forwards traffic or holds real credentials."""
import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

class NativeWire:
    def __init__(self,respond):
        self.requests=[];self.respond=respond;wire=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));wire.requests.append(body)
                try:
                    message=wire.respond(body)
                    if isinstance(message,str):message={'role':'assistant','content':message}
                    choices=[{'index':0,'message':message,'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop'}]
                    payload={'id':'offline-native-'+str(len(wire.requests)),'model':body['model'],'choices':choices,
                        'usage':{'prompt_tokens':20,'completion_tokens':10,'total_tokens':30,'cost':0}}
                    if body.get('stream'):
                        choices[0]['delta']=choices[0].pop('message');payload['object']='chat.completion.chunk'
                        raw=('data: '+json.dumps(payload)+'\n\ndata: [DONE]\n\n').encode();kind='text/event-stream'
                    else:raw=json.dumps(payload).encode();kind='application/json'
                    self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                except Exception as exc:
                    self.send_error(500,type(exc).__name__)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.endpoint='http://127.0.0.1:'+str(self.server.server_port)
    def close(self):self.server.shutdown();self.server.server_close();self.thread.join(2)
