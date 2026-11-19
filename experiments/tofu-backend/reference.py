"""In-memory HTTPS positive control, never a deployed production backend."""
import datetime as dt,http.server,ipaddress,json,ssl,threading,urllib.parse
from pathlib import Path
import core,engine


class Store:
    def __init__(self):
        self.state=None;self.lock=None;self.history=[];self.events=[];self.mutex=threading.Lock()
    def acquire(self,value):
        with self.mutex:
            if self.lock is not None and self.lock['ID']!=value['ID']:
                self.events.append({'event':'lock_conflict','id':value['ID']});return 423,json.dumps(self.lock).encode()
            self.lock=dict(value);self.events.append({'event':'lock_acquired','id':value['ID']});return 200,b''
    def release(self,value):
        with self.mutex:
            if self.lock is None or self.lock['ID']!=value['ID']:return 409,json.dumps(self.lock or {}).encode()
            self.events.append({'event':'lock_released','id':value['ID']});self.lock=None;return 200,b''
    def write(self,lock_id,value):
        parsed=json.loads(value)
        with self.mutex:
            if self.lock is None or self.lock['ID']!=lock_id:return 409,b''
            if 'encrypted_data' not in parsed:return 400,b''
            self.state=value;self.history.append({'sha256':core.digest(value),'bytes':len(value)});return 200,b''



class Control:
    def __init__(self,root):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        self.root=core.owned_dir(root);self.store=Store()
        key=ec.generate_private_key(ec.SECP256R1());now=dt.datetime.now(dt.timezone.utc)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'RBX synthetic backend control')])
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=1))
            .not_valid_after(now+dt.timedelta(hours=4))
            .add_extension(x509.BasicConstraints(ca=True,path_length=0),critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),critical=False)
            .sign(key,hashes.SHA256()))
        self.cert_pem=cert.public_bytes(serialization.Encoding.PEM)
        core.private_write(self.root/'control-cert.pem',self.cert_pem)
        core.private_write(self.root/'control-key.pem',key.private_bytes(serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        owner=self
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,code,value=b''):
                self.send_response(code);self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(value)));self.end_headers();self.wfile.write(value)
            def body(self):
                size=int(self.headers.get('Content-Length','0'))
                if size>65536:raise ValueError('fixture_request_bound')
                return self.rfile.read(size)
            def route(self):
                if urllib.parse.urlsplit(self.path).path!='/synthetic-state':
                    self.reply(404);return False
                return True
            def do_GET(self):
                if not self.route():return
                with owner.store.mutex:self.reply(404 if owner.store.state is None else 200,owner.store.state or b'')
            def do_LOCK(self):
                if self.route():self.reply(*owner.store.acquire(json.loads(self.body())))
            def do_UNLOCK(self):
                if self.route():self.reply(*owner.store.release(json.loads(self.body())))
            def do_POST(self):
                if not self.route():return
                value=self.body();lock_id=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('ID',[None])[0]
                self.reply(*owner.store.write(lock_id,value))
        self.server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.minimum_version=ssl.TLSVersion.TLSv1_2
        tls.load_cert_chain(str(self.root/'control-cert.pem'),str(self.root/'control-key.pem'))
        self.server.socket=tls.wrap_socket(self.server.socket,server_side=True)
        self.address='https://127.0.0.1:'+str(self.server.server_port)+'/synthetic-state'
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def configure(self,binding,name):
        working=engine.configure(binding,self.root/'engines',name)
        path=working/'fixture.tf.json';config=json.loads(path.read_text())
        config['terraform']['backend']={'http':{'address':self.address,'lock_address':self.address,
           'unlock_address':self.address,'client_ca_certificate_pem':self.cert_pem.decode(),
           'retry_max':0}}
        path.write_text(json.dumps(config,indent=2))
        (working/'fixture-guard.json').unlink()
        engine.seal(working)
        return working
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
