import React, { useEffect, useState } from 'react';

const API = '/api';

type StatusData = { checks: Record<string,boolean>; versions: Record<string,string> };
type Checkpoint = { id: number; label: string; created_at: string };
type Txn = { id: number; status: string; label: string; created_at: string };

const STATUS_MAP: Record<string,string> = {
  PASS: '#22c55e', WARN: '#f59e0b', FAIL: '#ef4444',
  SKIP: '#6b7280', UNREACHABLE: '#94a3b8'
};

function App() {
  const [page, setPage] = useState('dashboard');
  const [status, setStatus] = useState<StatusData|null>(null);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [txns, setTxns] = useState<Txn[]>([]);
  const [token, setToken] = useState('');

  useEffect(() => {
    fetch(`${API}/session`).then(r=>r.json()).then(d=>setToken(d.token));
    fetch(`${API}/status`).then(r=>r.json()).then(d=>setStatus(d));
    fetch(`${API}/checkpoints`).then(r=>r.json()).then(d=>setCheckpoints(d.checkpoints||[]));
    fetch(`${API}/transactions`).then(r=>r.json()).then(d=>setTxns(d.transactions||[]));
  }, []);

  const h = { headers: { 'X-Session-Token': token } };

  return (
    <div style={{fontFamily:'system-ui,sans-serif',maxWidth:960,margin:'0 auto',padding:16}}>
      <h1>🛡️ AgentState Guard</h1>
      <nav style={{display:'flex',gap:8,marginBottom:16,flexWrap:'wrap'}}>
        {['dashboard','checkpoints','transactions','settings','devices'].map(p=>
          <button key={p} onClick={()=>setPage(p)}
            style={{background:p===page?'#2563eb':'#e5e7eb',color:p===page?'#fff':'#000',
              border:'none',padding:'6px 14px',borderRadius:6,cursor:'pointer'}}>{p}</button>
        )}
      </nav>

      {page==='dashboard' && <div>
        <h2>Health</h2>
        {status?.checks && <div style={{display:'flex',gap:8,flexWrap:'wrap'}}>
          {Object.entries(status.checks).map(([k,v])=>
            <div key={k} style={{padding:'8px 12px',background:v?'#dcfce7':'#fee2e2',
              borderRadius:8,fontSize:14}}>{k}: {v?'✅':'❌'}</div>
          )}
        </div>}
        <h2>Versions</h2>
        <pre style={{background:'#f3f4f6',padding:12,borderRadius:8,fontSize:13}}>
          {JSON.stringify(status?.versions??{},null,2)}
        </pre>
        {checkpoints.length>0 && <div>
          <h2>Checkpoints ({checkpoints.length})</h2>
          {checkpoints.slice(0,5).map(c=>
            <div key={c.id} style={{padding:6,borderBottom:'1px solid #e5e7eb',fontSize:14}}>
              #{c.id} {c.label} <span style={{color:'#6b7280'}}>{c.created_at.slice(0,19)}</span>
            </div>
          )}
        </div>}
      </div>}

      {page==='checkpoints' && <div>
        <h2>All Checkpoints</h2>
        {checkpoints.map(c=>
          <div key={c.id} style={{padding:8,border:'1px solid #e5e7eb',borderRadius:8,marginBottom:6}}>
            #{c.id} <strong>{c.label}</strong> <span style={{color:'#6b7280'}}>{c.created_at.slice(0,19)}</span>
          </div>
        )}
      </div>}

      {page==='transactions' && <div>
        <h2>Transactions</h2>
        {txns.map(t=>
          <div key={t.id} style={{padding:8,border:'1px solid #e5e7eb',borderRadius:8,marginBottom:6}}>
            #{t.id} <strong>{t.label}</strong>
            <span style={{marginLeft:8,color:'#6b7280'}}>[{t.status}]</span>
          </div>
        )}
      </div>}

      {page==='settings' && <AISettingsScreen />}
      {page==='devices' && <DevicesScreen setPage={setPage} />}
    </div>
  );
}
export default App;

// ── AI Settings Screen ──────────────────────────────

type AIProvider = 'deepseek' | 'openai_compatible' | 'custom';

function AISettingsScreen() {
  const [provider, setProvider] = useState<AIProvider>('deepseek');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [model, setModel] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [testResult, setTestResult] = useState('');

  const presets: Record<AIProvider, { name: string; url: string }> = {
    deepseek: { name: 'DeepSeek', url: 'https://api.deepseek.com/v1' },
    openai_compatible: { name: 'OpenAI-Compatible', url: '' },
    custom: { name: 'Custom', url: '' },
  };

  useEffect(() => {
    setBaseUrl(presets[provider].url);
  }, [provider]);

  const testConnection = async () => {
    try {
      const resp = await fetch(baseUrl + '/models', {
        headers: { Authorization: 'Bearer ' + apiKey }
      });
      const data = await resp.json();
      setTestResult(resp.ok ? `Connected (${data.data?.length || 0} models)` : 'Failed: ' + resp.status);
    } catch(e) {
      setTestResult('Connection failed');
    }
  };

  return (
    <div style={{maxWidth: 480, margin: '16px auto'}}>
      <h2>AI Provider Settings</h2>
      <label>Provider</label>
      <select value={provider} onChange={e => setProvider(e.target.value as AIProvider)}>
        {Object.entries(presets).map(([k,v]) => <option key={k} value={k}>{v.name}</option>)}
      </select>
      <label>Base URL</label>
      <input value={baseUrl} onChange={e => setBaseUrl(e.target.value)} placeholder="https://api.deepseek.com/v1" />
      <label>API Key</label>
      <div style={{display:'flex', gap:8}}>
        <input type={showKey ? 'text' : 'password'} value={apiKey} onChange={e => setApiKey(e.target.value)}
               placeholder="sk-..." style={{flex:1}} />
        <button onClick={() => setShowKey(!showKey)}>{showKey ? 'Hide' : 'Show'}</button>
      </div>
      <label>Model</label>
      <div style={{display:'flex', gap:8}}>
        <input value={model} onChange={e => setModel(e.target.value)} placeholder="deepseek-chat" style={{flex:1}} />
        <button onClick={testConnection}>Test</button>
      </div>
      {testResult && <div style={{marginTop: 8, color: testResult.includes('Connected') ? 'green' : 'red'}}>{testResult}</div>}
      <p style={{color: '#6b7280', fontSize: 13, marginTop: 16}}>
        API Key is held in memory only. Never saved to disk. Never sent to Android.
      </p>
    </div>
  );
}

// ── Devices / Pairing Screen ──────────────────────

function DevicesScreen({ setPage }: { setPage: (p: string) => void }) {
  const [mobileLinkEnabled, setMobileLinkEnabled] = useState(false);
  const [pairingSession, setPairingSession] = useState<any>(null);
  const [sas, setSas] = useState('');

  const enableMobileLink = async () => {
    setMobileLinkEnabled(true);
    // Start pairing session via API
    try {
      const resp = await fetch('/device/v1/pair/start', { method: 'POST' });
      const data = await resp.json();
      setPairingSession(data);
    } catch(e) {}
  };

  const computeSas = async (androidPubkey: string) => {
    try {
      const resp = await fetch('/api/device-link/pair/sas', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pubkey_der_hex: androidPubkey })
      });
      const data = await resp.json();
      setSas(data.sas || '');
    } catch(e) {}
  };

  return (
    <div style={{maxWidth: 480, margin: '0 auto', padding: 16}}>
      <h2>Devices</h2>
      {!mobileLinkEnabled ? (
        <>
          <p>Mobile Link: OFF</p>
          <button onClick={enableMobileLink} style={{background:'#2563eb',color:'#fff',padding:'10px 20px',border:'none',borderRadius:8}}>
            Enable Mobile Link
          </button>
        </>
      ) : (
        <>
          <p>Mobile Link: ON</p>
          <p>No paired devices</p>
          <p style={{fontSize:13,color:'#6b7280'}}>
            Open AgentState Guard on your Android phone and scan the QR code to pair.
          </p>
          {pairingSession && (
            <div style={{background:'#f0f9ff',padding:16,borderRadius:8,marginTop:16,textAlign:'center'}}>
              <p style={{fontWeight:'bold'}}>Pairing Code: {pairingSession.session_id?.slice(0,16) || '...'}</p>
              {sas && <p style={{fontSize:24,letterSpacing:4,fontWeight:'bold'}}>{sas}</p>}
            </div>
          )}
          <button onClick={enableMobileLink} style={{marginTop:8}}>Refresh Pairing Code</button>
        </>
      )}
      <button onClick={() => setPage('dashboard')} style={{marginTop:16,display:'block'}}>Back to Dashboard</button>
    </div>
  );
}
