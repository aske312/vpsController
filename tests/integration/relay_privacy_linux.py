"""Manual Linux acceptance lab with synthetic keys and disposable network state.

Stage this file as prototype.py, product_unit_check.py, tests/api/relay_private_worker.py,
the two Relay privacy API modules and their standard API dependencies, plus Agent
agent.py/addresses.py into a private directory. Provide verified Linux sing-box,
Mihomo and wg binaries as ./sing-box, ./mihomo and ./wg. Run with root only inside
an authorized private network AND mount namespace, with loopback up and
VPS_CONTROL_ISOLATED_LAB=1. No working client profile or peer is used.
The systemd fixture creates one unique unit joined to this private network and
removes it in finally. Output contains synthetic test observations only.
"""
import base64,hashlib,http.server,json,os,secrets,socket,ssl,struct,subprocess,threading,time,urllib.request,uuid
import relay_privacy
import relay_private_transport
from pathlib import Path

if os.environ.get('VPS_CONTROL_ISOLATED_LAB') != '1' or os.stat('/proc/self/ns/net').st_ino == os.stat('/proc/1/ns/net').st_ino:
 raise SystemExit('Disposable private network namespace required')

ROOT=Path.cwd();BOX=str(ROOT/'sing-box');MIHOMO=str(ROOT/'mihomo')
ORIGIN='8.8.8.8';RELAY='8.8.4.4';EDGE=20443;BACK=19443
METHOD='2022-blake3-aes-128-gcm';KEY_A=secrets.token_urlsafe(32);KEY_B=secrets.token_urlsafe(32);KEY_C=base64.b64encode(secrets.token_bytes(16)).decode()
MARKER=b'PRIVATE-ORIGIN-CONTENT-NOT-FOR-WIRE';SNI='origin-private.example';BLOB=MARKER+os.urandom(4*1024*1024)
records=[];processes=[]
def emit(kind,**data):
 row={'kind':kind,**data};records.append(row);print(json.dumps(row),flush=True)
def write(name,value):
 path=ROOT/name;path.write_text(json.dumps(value));path.chmod(0o600);return path
def start(kind,path):
 log=(ROOT/(kind+'.log')).open('ab');args=[BOX,'run','-c',str(path)]
 if kind=='relay':
  profile=dict(PROFILE,origin_password=json.loads(path.read_text())['outbounds'][0]['password']);path=write('agent-request.json',profile);args=['python3','relay_private_worker.py',str(path)]
 p=subprocess.Popen(args,stdout=log,stderr=log);log.close();processes.append(p);time.sleep(.8 if kind=='relay' else .15)
 if p.poll() is not None:raise RuntimeError(kind+' startup: '+(ROOT/(kind+'.log')).read_text()[-600:])
 return p
class Handler(http.server.BaseHTTPRequestHandler):
 protocol_version='HTTP/1.1'
 def log_message(self,*args):pass
 def do_GET(self):
  body=BLOB if self.path=='/blob' else MARKER
  self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers()
  try:self.wfile.write(body)
  except (OSError,ssl.SSLError):pass
def udp_echo():
 sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind((ORIGIN,30002))
 while True:
  try:data,addr=sock.recvfrom(65535);sock.sendto(data,addr)
  except OSError:return
def allowed(tag,outbound):
 return [{'inbound':[tag],'ip_cidr':[ORIGIN+'/32'],'port':[30001,30003,30007,30008,30009,30011,34000],'network':'tcp','action':'route','outbound':outbound},
         {'inbound':[tag],'ip_cidr':[ORIGIN+'/32'],'port':[30002,30004,30005,30006,30009,34000],'network':'udp','action':'route','outbound':outbound}]
def ss_node(key,host,port,tag):return {'type':'shadowsocks','tag':tag,'server':host,'server_port':port,'method':METHOD,'password':key,'udp_fragment':True}
def ss_in(key,host,port,tag):return {'type':'shadowsocks','tag':tag,'listen':host,'listen_port':port,'method':METHOD,'password':key,'udp_fragment':True}
def wg_keys(name):
 path=ROOT/(name+'.pem');subprocess.run(['openssl','genpkey','-algorithm','X25519','-out',str(path)],check=True,capture_output=True)
 private=subprocess.check_output(['openssl','pkey','-in',str(path),'-outform','DER'])[-32:]
 public=subprocess.check_output(['openssl','pkey','-in',str(path),'-pubout','-outform','DER'])[-32:]
 return base64.b64encode(private).decode(),base64.b64encode(public).decode()
def relay_config(key=KEY_B):
 value=dict(PROFILE,origin_password=key)
 return relay_privacy.render_relay(value,ROOT)
def mihomo(proxy=None,key=None,port=1080,wrapped=True,pin=None):
 if key is None:key=KEY_A
 outer=dict(relay_privacy.mihomo_transport(PROFILE),name='outer',password=key)
 if pin is not None:outer['fingerprint']=pin
 nodes=[outer];selected='outer'
 if proxy:
  inner=dict(proxy,**{'name':'inner'});selected='inner'
  if wrapped:
   endpoint={'ip':proxy['server'],'port':proxy['port'],'network':'udp' if proxy['type'] in ('hysteria2','tuic','wireguard') else 'tcp'}
   inner=relay_privacy.protect_node(inner,PROFILE,endpoint);inner['dialer-proxy']='outer'
  nodes.insert(0,inner)
 cfg={'mixed-port':port,'bind-address':'127.0.0.1','allow-lan':False,'log-level':'warning','mode':'rule','ipv6':False,'dns':{'enable':False},'tun':{'enable':False},'proxies':nodes,'rules':['MATCH,'+selected]}
 path=write('mh-'+str(port)+'.json',cfg);log=(ROOT/('mh-'+str(port)+'.log')).open('ab')
 p=subprocess.Popen([MIHOMO,'-d',str(ROOT/('mh-'+str(port))),'-f',str(path)],stdout=log,stderr=log);log.close();processes.append(p)
 for _ in range(100):
  if p.poll() is not None:raise RuntimeError('Mihomo exited')
  try:
   with socket.create_connection(('127.0.0.1',port),timeout=.05):return p
  except OSError:time.sleep(.05)
 raise RuntimeError('Mihomo listener unavailable')
def download(port=1080,path='/blob',target=30001,expect=True,timeout=10):
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({'http':'http://127.0.0.1:'+str(port)}));t=time.monotonic()
 try:
  with opener.open('http://'+ORIGIN+':'+str(target)+path,timeout=timeout) as r:body=r.read()
  success=body==(BLOB if path=='/blob' else MARKER)
 except Exception as exc:success=False;body=b'';failure=type(exc).__name__+': '+str(exc)
 if expect is not None and success!=expect:raise AssertionError('Transfer expectation failed '+locals().get('failure','invalid_body'))
 return {'ok':success,'ms':round((time.monotonic()-t)*1000),'bytes':len(body)}
def socks_udp(port=1080,size=1200):
 control=socket.create_connection(('127.0.0.1',port),timeout=3);control.sendall(b'\x05\x01\x00');assert control.recv(2)==b'\x05\x00';control.sendall(b'\x05\x03\x00\x01'+bytes(6));reply=control.recv(256);assert reply[:2]==b'\x05\x00'
 if reply[3]==1:host=socket.inet_ntoa(reply[4:8]);p=struct.unpack('!H',reply[8:10])[0]
 elif reply[3]==4:host='127.0.0.1';p=struct.unpack('!H',reply[20:22])[0]
 else:raise AssertionError('SOCKS UDP address type')
 if host=='0.0.0.0':host='127.0.0.1'
 body=(MARKER+secrets.token_bytes(size))[:size];header=b'\x00\x00\x00\x01'+socket.inet_aton(ORIGIN)+struct.pack('!H',30002)
 with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
  sock.settimeout(3);sock.sendto(header+body,(host,p));response=sock.recv(65535)
  assert response[10:]==body,'UDP payload changed'
 control.close();return len(body)
stop=threading.Event();capture={'edge_packets':0,'backhaul_packets':0,'edge_marker':False,'backhaul_marker':False,'plain_control_marker':False}
def sniff():
 sock=socket.socket(socket.AF_PACKET,socket.SOCK_RAW,socket.htons(0x0800));sock.settimeout(.2)
 try:
  while not stop.is_set():
   try:packet=sock.recv(65535)
   except socket.timeout:continue
   if len(packet)<38:continue
   ip=packet[14:];ihl=(ip[0]&15)*4;proto=ip[9]
   if proto not in (6,17) or len(ip)<ihl+8:continue
   segment=ip[ihl:];src,dst=struct.unpack('!HH',segment[:4]);payload=segment[(segment[12]>>4)*4:] if proto==6 else segment[8:]
   marker=MARKER in payload or SNI.encode() in payload
   for kind,port in [('edge',EDGE),('backhaul',BACK)]:
    if port in (src,dst):capture[kind+'_packets']+=1;capture[kind+'_marker']|=marker
   if 30001 in (src,dst):capture['plain_control_marker']|=marker
 finally:sock.close()
try:
 subprocess.run(['ip','link','set','lo','mtu','1500'],check=True)
 subprocess.run(['ip','link','add','lab0','type','dummy'],check=True)
 for address in (ORIGIN,RELAY):subprocess.run(['ip','addr','add',address+'/32','dev','lab0'],check=True)
 subprocess.run(['ip','link','set','lab0','up'],check=True)
 httpd=http.server.ThreadingHTTPServer((ORIGIN,30001),Handler);threading.Thread(target=httpd.serve_forever,daemon=True).start();threading.Thread(target=udp_echo,daemon=True).start()
 foreign=http.server.ThreadingHTTPServer((ORIGIN,30012),Handler);threading.Thread(target=foreign.serve_forever,daemon=True).start()
 subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN='+SNI,'-addext','subjectAltName=DNS:'+SNI,'-keyout','server.key','-out','server.crt'],capture_output=True,check=True)
 subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-subj','/CN=endpoint','-addext','subjectAltName=IP:'+ORIGIN+',IP:'+RELAY,'-keyout','outer.key','-out','outer.crt'],capture_output=True,check=True)
 outer_fingerprint=hashlib.sha256(ssl.PEM_cert_to_DER_cert((ROOT/'outer.crt').read_text())).hexdigest()
 outer_tls={'enabled':True,'alpn':['h3'],'certificate_path':str(ROOT/'outer.crt'),'key_path':str(ROOT/'outer.key')}
 for old,new in [('outer.crt','origin.crt'),('outer.crt','ingress.crt'),('outer.key','origin.key'),('outer.key','ingress.key')]:
  (ROOT/new).write_bytes((ROOT/old).read_bytes())
 PROFILE={'schema':1,'transport':'hysteria2','generation':secrets.token_hex(16),'relay_ip':RELAY,'listen_port':EDGE,'ingress_password':KEY_A,'ingress_certificate':(ROOT/'outer.crt').read_text(),'ingress_private_key':(ROOT/'outer.key').read_text(),'origin_ip':ORIGIN,'origin_port':BACK,'origin_password':KEY_B,'origin_certificate':(ROOT/'outer.crt').read_text(),'bandwidth_mbps':1000,'targets':[{'ip':ORIGIN,'port':p,'network':n} for n,ps in [('tcp',[30001,30003,30007,30008,30009,30011,34000]),('udp',[30002,30004,30005,30006,30009,34000])] for p in ps]}
 tls={'enabled':True,'server_name':SNI,'certificate_path':str(ROOT/'server.crt'),'key_path':str(ROOT/'server.key')}
 inner_password=secrets.token_urlsafe(24);baseline_password=secrets.token_urlsafe(24);tuic_id=str(uuid.uuid4());baseline_id=str(uuid.uuid4())
 backhaul_in={'type':'hysteria2','tag':'backhaul','listen':ORIGIN,'listen_port':BACK,'users':[{'password':KEY_B}],'up_mbps':1000,'down_mbps':1000,'tls':outer_tls}
 inbounds=[backhaul_in,{'type':'trojan','tag':'trojan','listen':ORIGIN,'listen_port':30003,'users':[{'password':inner_password}],'tls':tls},
  {'type':'hysteria2','tag':'hy2','listen':ORIGIN,'listen_port':30004,'users':[{'name':'fixture','password':inner_password},{'name':'baseline','password':baseline_password}],'up_mbps':1000,'down_mbps':1000,'tls':dict(tls,alpn=['h3'])},
  {'type':'tuic','tag':'tuic','listen':ORIGIN,'listen_port':30005,'users':[{'uuid':tuic_id,'password':inner_password},{'uuid':baseline_id,'password':baseline_password}],'congestion_control':'bbr','tls':dict(tls,alpn=['h3'])}]
 inbounds += [
  {'type':'vless','tag':'vless','listen':ORIGIN,'listen_port':30007,'users':[{'uuid':tuic_id}],'tls':tls},
  {'type':'vmess','tag':'vmess','listen':ORIGIN,'listen_port':30008,'users':[{'uuid':tuic_id,'alterId':0}],'tls':tls},
  {'type':'shadowsocks','tag':'ss','listen':ORIGIN,'listen_port':30009,'method':METHOD,'password':KEY_C}]
 rules=allowed('backhaul','direct')
 for tag in ('trojan','hy2','tuic','vless','vmess','ss'):rules+=allowed(tag,'direct')
 wg_server_key,wg_server_public=wg_keys('wg-server');wg_clients=[wg_keys('wg-client-'+str(n)) for n in range(8)]
 subprocess.run(['ip','link','add','wg-test','type','wireguard'],check=True)
 wg_config='[Interface]\nPrivateKey = '+wg_server_key+'\nListenPort = 30006\n'
 for n,pair in enumerate(wg_clients):wg_config+='[Peer]\nPublicKey = '+pair[1]+'\nAllowedIPs = 10.88.0.'+str(n+2)+'/32\n'
 (ROOT/'wg.conf').write_text(wg_config);(ROOT/'wg.conf').chmod(0o600)
 subprocess.run([str(ROOT/'wg'),'setconf','wg-test',str(ROOT/'wg.conf')],check=True)
 subprocess.run(['ip','addr','add','10.88.0.1/24','dev','wg-test'],check=True)
 subprocess.run(['ip','link','set','wg-test','mtu','1280','up'],check=True)
 origin_config=relay_privacy.render_origin(PROFILE,ROOT);origin_config['inbounds']+=inbounds[1:];origin_config['route']['rules']=rules+[{'action':'reject'}]
 import product_unit_check;product_unit_check.check(ROOT,PROFILE,Path(BOX));emit('product_systemd',ok=True)
 origin=start('origin',write('origin.json',origin_config));relay=start('relay',write('relay.json',relay_config()))
 sniffer=threading.Thread(target=sniff,daemon=True);sniffer.start()
 opener=urllib.request.build_opener(urllib.request.ProxyHandler({}));t=time.monotonic()
 with opener.open('http://'+ORIGIN+':30001/blob') as r:assert r.read()==BLOB
 emit('direct',ms=round((time.monotonic()-t)*1000),bytes=len(BLOB))
 client=mihomo();emit('outer_tcp',**download())
 relay_private_transport.probe(ROOT,PROFILE,Path(BOX),34000);emit('product_probe',ok=True);emit('outer_udp',sizes=[socks_udp(size=n) for n in (32,1200,1420,4096)])
 for kind,port in [('trojan',30003),('hysteria2',30004),('tuic',30005),('vless',30007),('vmess',30008),('ss',30009)]:
  proxy={'type':kind,'server':ORIGIN,'port':port,'password':inner_password,'sni':SNI,'skip-cert-verify':True}
  if kind=='tuic':proxy.update(uuid=tuic_id,**{'congestion-controller':'bbr','alpn':['h3'],'disable-mtu-discovery':True})
  if kind=='hysteria2':proxy.update(up=1000,down=1000,alpn=['h3'])
  if kind in ('vless','vmess'):proxy.update(uuid=tuic_id,tls=True,servername=SNI,cipher='auto',alterId=0)
  if kind=='ss':proxy.update(cipher=METHOD,password=KEY_C,udp=True)
  baseline=dict(proxy)
  if kind in ('hysteria2','tuic'):baseline['password']=baseline_password
  if kind=='tuic':baseline['uuid']=baseline_id
  direct=mihomo(baseline,port=1083,wrapped=False);emit('inner_direct_'+kind,**download(1083));direct.terminate();direct.wait(timeout=5)
  p=mihomo(proxy,port=1081)
  emit('inner_'+kind,downloads=[download(1081) for _ in range(3)],udp_bytes=socks_udp(1081))
  p.terminate();p.wait(timeout=5)
 for n,(client_key,client_public) in enumerate(wg_clients):
  wg_proxy={'type':'wireguard','server':ORIGIN,'port':30006,'private-key':client_key,'public-key':wg_server_public,'ip':'10.88.0.'+str(n+2),'mtu':1280,'udp':True,'persistent-keepalive':25}
  wg=mihomo(wg_proxy,port=1081,wrapped=n%2==1);cold=download(1081,expect=None);emit('wireguard_cold_start',iteration=n,wrapped=bool(n%2),**cold);emit('inner_wireguard' if n%2 else 'baseline_wireguard',iteration=n,downloads=[download(1081) for _ in range(3)],udp_bytes=socks_udp(1081));wg.terminate();wg.wait(timeout=5)
 bad=mihomo(pin='0'*64,port=1082);emit('wrong_certificate_pin',**download(1082,expect=False));bad.terminate();bad.wait(timeout=5)
 bad=mihomo(key=secrets.token_urlsafe(32),port=1082);emit('wrong_ingress_key',**download(1082,expect=False));bad.terminate();bad.wait(timeout=5)
 relay.terminate();relay.wait(timeout=5);emit('relay_down_no_fallback',**download(expect=False,timeout=2));relay=start('relay',ROOT/'relay.json')
 began=time.monotonic();attempts=0
 while True:
  attempts+=1;row=download(expect=None,timeout=2)
  if row['ok']:break
  if time.monotonic()-began>70:raise AssertionError('Relay restart did not recover within70s')
  time.sleep(.5)
 emit('relay_restart',recovery_ms=round((time.monotonic()-began)*1000),attempts=attempts,**row)
 emit('denied_destination',**download(target=30012,expect=False))
 relay.terminate();relay.wait(timeout=5);relay=start('relay',write('relay-bad.json',relay_config(secrets.token_urlsafe(32))));emit('wrong_backhaul_key',**download(expect=False))
 stop.set();sniffer.join(timeout=2);assert capture['edge_packets']>0 and capture['backhaul_packets']>0
 assert not capture['edge_marker'] and not capture['backhaul_marker'] and capture['plain_control_marker'];emit('wire_privacy',**capture)
 emit('success',checks=len(records))
except Exception as exc:
 emit('failure',error_type=type(exc).__name__,message=str(exc))
 for log in ROOT.glob('*.log'):
  text=log.read_text(errors='replace')[-2500:]
  for key in (KEY_A,KEY_B,KEY_C,globals().get('inner_password',''),globals().get('baseline_password','')):
   if key:text=text.replace(key,'[synthetic-key]')
  emit('diagnostic',file=log.name,text=text)
 raise
finally:
 stop.set()
 for p in processes:
  if p.poll() is None:p.terminate()
 for p in processes:
  try:p.wait(timeout=5)
  except subprocess.TimeoutExpired:p.kill();p.wait()
