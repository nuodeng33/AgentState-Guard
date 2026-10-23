import { useEffect, useState } from 'react';
import type { ApiClient } from '../api/client';
import type { DiscoverySources } from '../api/types';
import { DiscoveryCatalog } from './DiscoveryCatalog';
import { useI18n } from '../i18n/I18nProvider';

export default function ProjectConnectionCatalog({ client, disabled, onRegistered }: {
  client: ApiClient; disabled: boolean; onRegistered: () => void;
}) {
  const { locale } = useI18n(); const zh = locale === 'zh-CN';
  const [open, setOpen] = useState(false);
  const [sources, setSources] = useState<DiscoverySources | null>(null);
  const [sourceId, setSourceId] = useState('');
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [scanning, setScanning] = useState(false);
  async function scan() {
    if (scanning || disabled) return;
    setScanning(true); setError(false);
    try { await client.post('/api/v1/discovery/refresh', {}); setRefresh(value => value + 1); onRegistered(); }
    catch { setError(true); }
    finally { setScanning(false); }
  }
  useEffect(() => {
    if (!open) return;
    let active = true; setError(false);
    void client.get<DiscoverySources>('/api/v1/discovery/sources').then(result => {
      if (!active) return;
      setSources(result);
      setSourceId(id => result.items.some(item => item.source_id === id) ? id : result.items[0]?.source_id ?? '');
    }).catch(() => { if (active) setError(true); });
    return () => { active = false; };
  }, [client, open, refresh]);
  const source = sources?.items.find(item => item.source_id === sourceId);
  return <details className="work-project-connection" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{zh ? '接入程序与会话库' : 'Connect programs and session stores'}</summary>
    <p>{zh ? '先选择环境，再登记该环境中的程序和会话库。登记完成后，在上方选择应用和目录；创建会话仍需单独授权。' : 'Choose an environment and register its program and session store. Then select the application and directory above. Creating a session requires separate consent.'}</p>
    {scanning && <p role="status">{zh ? '正在扫描环境' : 'Scanning environments'}</p>}
    {error && <p role="alert">{zh ? '无法读取环境，请重试。' : 'Unable to read environments. Retry.'}</p>}
    <button type="button" disabled={disabled || scanning} onClick={() => void scan()}>{zh ? '扫描程序与环境' : 'Scan programs and environments'}</button>
    <label>{zh ? '接入环境' : 'Connection environment'}<select disabled={disabled} value={sourceId} onChange={event => setSourceId(event.target.value)}>
      <option value="">{zh ? '选择环境' : 'Select environment'}</option>
      {sources?.items.map(item => <option key={item.source_id} value={item.source_id}>{item.label} · {item.source_id} · {item.state}</option>)}
    </select></label>
    {open && source && <fieldset disabled={disabled || scanning}><DiscoveryCatalog key={source.source_id} client={client} source={source}
      coverage={sources?.inventory_coverage?.find(item => item.source_id === source.source_id)} visible={open} refreshKey={refresh}
      zh={zh} locale={locale} onRegistered={onRegistered} /></fieldset>}
  </details>;
}
