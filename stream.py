import subprocess, threading, socketserver, http.server, sys, os, time
from urllib.parse import urlparse, parse_qs

FFMPEG = [
    'ffmpeg', '-loglevel', 'error', '-nostdin',
    '-f', 'v4l2', '-input_format', 'mjpeg',
    '-framerate', '30', '-video_size', '1280x720',
    '-i', '/dev/video0',
    '-c', 'copy', '-f', 'mjpeg', '-',
]

PAN_MIN, PAN_MAX, PAN_STEP = -522000, 522000, 3600
TILT_MIN, TILT_MAX, TILT_STEP = -324000, 360000, 3600
ZOOM_MIN, ZOOM_MAX = 100, 400

state = {'pan': 0, 'tilt': 0, 'zoom': 100}
state_lock = threading.Lock()
latest = [None, 0]
cv = threading.Condition()

def clamp(v, lo, hi, step=1):
    v = max(lo, min(hi, int(v)))
    return (v // step) * step

def apply_ptz():
    with state_lock:
        p, t, z = state['pan'], state['tilt'], state['zoom']
    try:
        subprocess.run(
            ['v4l2-ctl', '-d', '/dev/video0', '-c',
             f'pan_absolute={p},tilt_absolute={t},zoom_absolute={z}'],
            check=False, timeout=2,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        print(f'ptz err: {e}', file=sys.stderr)

def run_ffmpeg_once():
    p = subprocess.Popen(FFMPEG, stdout=subprocess.PIPE, stderr=sys.stderr, bufsize=0)
    buf = b''
    try:
        while True:
            chunk = p.stdout.read(65536)
            if not chunk:
                return
            buf += chunk
            while True:
                soi = buf.find(b'\xff\xd8')
                if soi < 0:
                    buf = b''
                    break
                eoi = buf.find(b'\xff\xd9', soi + 2)
                if eoi < 0:
                    buf = buf[soi:]
                    break
                frame = buf[soi:eoi+2]
                buf = buf[eoi+2:]
                with cv:
                    latest[0] = frame
                    latest[1] += 1
                    cv.notify_all()
    finally:
        try: p.kill()
        except Exception: pass
        try: p.wait(timeout=2)
        except Exception: pass

def reader():
    while True:
        while not os.path.exists('/dev/video0'):
            print('waiting for /dev/video0...', file=sys.stderr)
            time.sleep(1)
        print('starting ffmpeg', file=sys.stderr)
        try:
            run_ffmpeg_once()
        except Exception as e:
            print(f'ffmpeg err: {e}', file=sys.stderr)
        print('ffmpeg exited, restarting in 1s', file=sys.stderr)
        time.sleep(1)

HTML = br"""<!doctype html><html><head><meta charset="utf-8"><title>Link 2</title>
<style>
  html,body{margin:0;height:100%;background:#000;overflow:hidden;font-family:monospace;color:#0f0}
  #v{width:100vw;height:100vh;object-fit:contain;display:block;user-select:none;cursor:grab}
  #v.drag{cursor:grabbing}
  #hud{position:fixed;top:8px;left:8px;background:rgba(0,0,0,.6);padding:6px 10px;border-radius:4px;font-size:12px;pointer-events:none}
  #ctl{position:fixed;bottom:12px;left:50%;transform:translateX(-50%);background:rgba(0,0,0,.6);padding:8px 12px;border-radius:4px;font-size:12px;display:flex;gap:10px;align-items:center}
  button{background:#111;color:#0f0;border:1px solid #0f0;padding:4px 10px;font-family:inherit;cursor:pointer}
  button:hover{background:#0f0;color:#000}
</style></head>
<body>
<img id="v" src="/stream" draggable="false">
<div id="hud">pan 0 | tilt 0 | zoom 1.0x</div>
<div id="ctl">
  <button onclick="ptz(0,0,100,true)">center</button>
  <span>drag video to pan/tilt &middot; scroll to zoom</span>
</div>
<script>
let pan=0, tilt=0, zoom=100;
const hud=document.getElementById('hud');
const v=document.getElementById('v');
function upd(){
  hud.textContent = 'pan '+(pan/3600).toFixed(0)+'\u00b0 | tilt '+(tilt/3600).toFixed(0)+'\u00b0 | zoom '+(zoom/100).toFixed(1)+'x';
}
let pending=false, last=0;
async function send(){
  if(pending) return;
  const now=performance.now();
  if(now-last<40){ setTimeout(send,40); return; }
  pending=true; last=now;
  try{ await fetch('/ptz?pan='+pan+'&tilt='+tilt+'&zoom='+zoom); }catch(e){}
  pending=false;
}
function ptz(p,t,z,abs){
  if(abs){ pan=p; tilt=t; zoom=z; }
  else { pan+=p; tilt+=t; zoom+=z; }
  pan=Math.max(-522000,Math.min(522000,pan));
  tilt=Math.max(-324000,Math.min(360000,tilt));
  zoom=Math.max(100,Math.min(400,zoom));
  upd(); send();
}
let dragging=false, sx=0, sy=0, span=0, stilt=0;
v.addEventListener('mousedown', e=>{ dragging=true; sx=e.clientX; sy=e.clientY; span=pan; stilt=tilt; v.classList.add('drag'); e.preventDefault(); });
window.addEventListener('mouseup', ()=>{ dragging=false; v.classList.remove('drag'); });
window.addEventListener('mousemove', e=>{
  if(!dragging) return;
  const dx=e.clientX-sx, dy=e.clientY-sy;
  const zf=zoom/100;
  const panScale=300000/zf;
  const tiltScale=180000/zf;
  pan = span - Math.round(dx*panScale/v.clientWidth);
  tilt = stilt + Math.round(dy*tiltScale/v.clientHeight);
  ptz(0,0,0,false);
});
v.addEventListener('wheel', e=>{ e.preventDefault(); ptz(0,0,e.deltaY<0?20:-20,false); }, {passive:false});
upd();
</script>
</body></html>"""

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Cache-Control', 'no-cache, private')
            self.end_headers()
            seq = 0
            try:
                while True:
                    with cv:
                        while latest[1] == seq:
                            cv.wait(timeout=5)
                        f = latest[0]; seq = latest[1]
                    self.wfile.write(b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n' % len(f))
                    self.wfile.write(f); self.wfile.write(b'\r\n')
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        elif u.path == '/ptz':
            q = parse_qs(u.query)
            with state_lock:
                if 'pan' in q: state['pan'] = clamp(q['pan'][0], PAN_MIN, PAN_MAX, PAN_STEP)
                if 'tilt' in q: state['tilt'] = clamp(q['tilt'][0], TILT_MIN, TILT_MAX, TILT_STEP)
                if 'zoom' in q: state['zoom'] = clamp(q['zoom'][0], ZOOM_MIN, ZOOM_MAX)
                p, t, z = state['pan'], state['tilt'], state['zoom']
            threading.Thread(target=apply_ptz, daemon=True).start()
            body = ('{"pan":' + str(p) + ',"tilt":' + str(t) + ',"zoom":' + str(z) + '}').encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(HTML)))
            self.end_headers()
            self.wfile.write(HTML)

class TS(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

threading.Thread(target=reader, daemon=True).start()
print('listening :8090', file=sys.stderr)
TS(('0.0.0.0', 8090), H).serve_forever()
