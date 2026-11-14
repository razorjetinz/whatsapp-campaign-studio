const state = { view: 'overview', settings: null, contacts: [], groups: [], campaigns: [], templates: [], templateError: '', selectedCampaign: null };
const root = document.getElementById('view-root');
const toastNode = document.getElementById('toast');

function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function fmtDate(value) { if (!value) return '—'; try { return new Date(value).toLocaleString(); } catch { return value; } }
function statusPill(status) {
  const styles = {completed:'green',completed_with_errors:'orange',sending:'blue',queued:'blue',draft:'',paused:'orange',failed:'red'};
  const labels = {completed:'Completed',completed_with_errors:'Completed with errors',sending:'Sending',queued:'Queued',draft:'Draft',paused:'Paused',failed:'Failed'};
  return `<span class="pill ${styles[status] || ''}">${esc(labels[status] || status)}</span>`;
}
function showToast(message, error=false) {
  toastNode.textContent = message; toastNode.classList.remove('hidden','error'); if (error) toastNode.classList.add('error');
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toastNode.classList.add('hidden'), 4800);
}
async function api(path, options={}) {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && options.body && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  if (['POST','PUT','PATCH','DELETE'].includes((options.method || 'GET').toUpperCase())) headers.set('X-Requested-With','WhatsAppCampaignStudio');
  const response = await fetch(path, {...options, headers});
  let data = {}; try { data = await response.json(); } catch {}
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
async function loadAll() {
  const [settings, contacts, groups, campaigns] = await Promise.all([
    api('/api/settings'), api('/api/contacts'), api('/api/groups'), api('/api/campaigns')
  ]);
  state.settings=settings; state.contacts=contacts; state.groups=groups; state.campaigns=campaigns;
  const banner = document.getElementById('mode-banner');
  banner.className = `mode-banner ${settings.dry_run ? 'dry' : 'live'}`;
  banner.innerHTML = settings.dry_run
    ? '<strong>Dry-run mode is ON.</strong> Campaigns are simulated; no WhatsApp messages leave this application. Configure Meta credentials and set <span class="code">DRY_RUN=false</span> only when ready for live sending.'
    : '<strong>Live sending is enabled.</strong> Campaigns can send real WhatsApp messages. Confirm consent, template approval and recipient counts before launching.';
  render();
}
function setView(view) {
  state.view=view;
  document.querySelectorAll('.nav-item').forEach(btn => btn.classList.toggle('active', btn.dataset.view===view));
  document.getElementById('page-title').textContent = ({overview:'Overview',contacts:'Contacts',groups:'Contact groups',campaigns:'Campaigns',templates:'Message templates',settings:'Settings'})[view];
  render();
}
document.querySelectorAll('.nav-item').forEach(btn => btn.addEventListener('click', () => setView(btn.dataset.view)));

function statCard(label, value, caption, icon) {
  return `<div class="stat-card"><div class="stat-top"><span>${label}</span><span class="stat-icon">${icon}</span></div><div class="stat-value">${value}</div><div class="stat-caption">${caption}</div></div>`;
}
function renderOverview() {
  const eligible=state.contacts.filter(c=>c.opted_in && !c.unsubscribed_at).length;
  const completed=state.campaigns.filter(c=>c.status==='completed'||c.status==='completed_with_errors').length;
  const sent=state.campaigns.reduce((n,c)=>n+(c.sent||0),0);
  const rows=state.campaigns.slice(0,5);
  return `<div class="page-intro"><div><h2>Your messaging at a glance</h2><p>Manage consented contacts and keep campaigns organised.</p></div><div class="actions"><button class="btn primary" data-action="new-campaign">＋ New campaign</button></div></div>
  <div class="stats-grid">${statCard('Total contacts',state.contacts.length,'Imported or added','♙')}${statCard('Active consent',eligible,'Eligible for campaigns','✓')}${statCard('Campaigns',state.campaigns.length,'All campaign records','➤')}${statCard('Messages sent',sent,'Accepted by Meta / simulated','↗')}</div>
  <div class="dashboard-grid"><section class="panel"><div class="panel-header"><div><h3>Recent campaigns</h3><p>Latest activity in this workspace</p></div><button class="text-link" data-view="campaigns">View all →</button></div>
  ${rows.length?`<div class="table-wrap"><table><thead><tr><th>CAMPAIGN</th><th>RECIPIENT GROUP</th><th>PROGRESS</th><th>STATUS</th></tr></thead><tbody>${rows.map(c=>`<tr><td><div class="primary-cell">${esc(c.name)}</div><div class="secondary-cell">${fmtDate(c.created_at)}</div></td><td>${esc(c.group_name)}</td><td>${c.sent||0} / ${c.total||0}</td><td>${statusPill(c.status)}</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty-state"><div class="empty-icon">➤</div><strong>No campaigns yet</strong><p>Create a contact group, choose an approved WhatsApp template and prepare your first campaign.</p><button class="btn primary" data-action="new-campaign">Create campaign</button></div>`}
  </section><section class="panel"><div class="panel-header"><div><h3>Getting started</h3><p>Complete these steps to go live</p></div></div><div class="setup-list">
  <div class="setup-row"><div class="setup-check">✓</div><div><strong>Secure your workspace</strong><span>Dashboard password and webhook token are required.</span></div></div>
  <div class="setup-row"><div class="setup-check ${eligible?'':'pending'}">${eligible?'✓':'1'}</div><div><strong>Import consented contacts</strong><span>Keep a record of how each person opted in.</span></div></div>
  <div class="setup-row"><div class="setup-check ${state.groups.length?'':'pending'}">${state.groups.length?'✓':'2'}</div><div><strong>Organise contact groups</strong><span>Campaigns are sent to a selected group only.</span></div></div>
  <div class="setup-row"><div class="setup-check ${state.settings.meta_configured?'':'pending'}">${state.settings.meta_configured?'✓':'3'}</div><div><strong>Connect Meta Cloud API</strong><span>Configure credentials in your local .env file.</span></div></div>
  <div class="setup-row"><div class="setup-check ${completed?'':'pending'}">${completed?'✓':'4'}</div><div><strong>Run your first campaign</strong><span>Start with dry-run mode before live sending.</span></div></div>
  </div><div class="notice mt-12"><strong>Consent-first messaging.</strong> Only contact people who have opted in to receive WhatsApp messages from your business. Opt-outs are excluded from future campaigns.</div></section></div>`;
}
function renderContacts() {
  return `<div class="page-intro"><div><h2>Contact directory</h2><p>Import CSV contacts or add them individually. Only active, consented contacts are eligible to receive campaigns.</p></div><div class="actions"><button class="btn" data-action="show-add-contact">＋ Add contact</button></div></div>
  <div class="content-grid"><section class="panel"><div class="panel-header"><div><h3>All contacts <span class="pill">${state.contacts.length}</span></h3><p>Showing up to 500 records</p></div></div>
  ${state.contacts.length?`<div class="table-wrap"><table><thead><tr><th>CONTACT</th><th>WHATSAPP NUMBER</th><th>GROUPS</th><th>CONSENT</th><th>ACTION</th></tr></thead><tbody>${state.contacts.map(c=>`<tr><td><div class="contact-name"><div class="contact-avatar">${esc((c.name||'?').slice(0,1).toUpperCase())}</div><div><div class="primary-cell">${esc(c.name||'Unnamed')}</div><div class="secondary-cell">Added ${fmtDate(c.created_at)}</div></div></div></td><td>${esc(c.phone)}</td><td>${esc(c.groups||'—')}</td><td>${c.opted_in&&!c.unsubscribed_at?'<span class="pill green">Opted in</span>':'<span class="pill red">Not eligible</span>'}</td><td>${c.opted_in&&!c.unsubscribed_at?`<button class="btn small danger" data-action="opt-out" data-id="${c.id}">Opt out</button>`:'<span class="muted">—</span>'}</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty-state"><div class="empty-icon">♙</div><strong>Your contact list is empty</strong><p>Upload a CSV with phone numbers in international format and an opted_in column.</p></div>`}
  </section><aside class="panel form-card"><h3>Import contacts</h3><p class="subtext">Upload a UTF-8 CSV. Required column: <span class="code">phone</span>. Consent is read from <span class="code">opted_in</span>.</p>
  <form id="import-form"><div class="field"><label for="csv-file">CSV file</label><input id="csv-file" name="file" type="file" accept=".csv,text/csv" required></div><div class="field"><label for="import-group">Add imported contacts to group</label><select id="import-group" name="group_id"><option value="">Do not assign a group</option>${state.groups.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('')}</select></div><button class="btn primary" type="submit">Import CSV</button></form>
  <div class="notice mt-12"><strong>CSV example</strong><br><span class="code">name,phone,opted_in,consent_source,company</span><br><span class="code">Asha,+919876543210,yes,Website form,Acme</span><br><br>Use <span class="code">yes</span> only when you have permission to message that person. Numbers must include the country code.</div>
  <div id="add-contact-form" class="hidden mt-12"><div class="panel-header"><h3>Add one contact</h3><button class="text-link" type="button" data-action="hide-add-contact">Close</button></div><form id="contact-form"><div class="field"><label>Name</label><input name="name" maxlength="160" placeholder="Contact name"></div><div class="field"><label>Phone in E.164 format</label><input name="phone" placeholder="+919876543210" required></div><div class="field"><label>Consent source</label><input name="consent_source" placeholder="Website signup form" required></div><div class="field"><label>Group (optional)</label><select name="group_id"><option value="">No group</option>${state.groups.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('')}</select></div><label class="check-label"><input name="opted_in" type="checkbox" required> I confirm this person explicitly opted in to receive WhatsApp messages.</label><button class="btn primary mt-12" type="submit">Save contact</button></form></div>
  </aside></div>`;
}
function renderGroups() {
  return `<div class="page-intro"><div><h2>Contact groups</h2><p>Organise contacts into focused audiences. Campaigns always target one selected group.</p></div></div>
  <div class="content-grid"><section><div class="group-grid">${state.groups.length?state.groups.map(g=>`<div class="group-card"><div class="group-card-top"><div class="group-symbol">▤</div><span class="pill green">Group</span></div><div class="group-count">${g.eligible_count}</div><div class="group-name">${esc(g.name)}</div><div class="group-caption">Contacts with active consent</div><div class="status-line"><span class="status-dot"></span> Available for campaigns</div></div>`).join(''):`<div class="panel empty-state"><div class="empty-icon">▤</div><strong>No groups created</strong><p>Create a group to organise opted-in contacts before setting up a campaign.</p></div>`}</div></section>
  <aside class="panel form-card"><h3>Create a group</h3><p class="subtext">Examples: Customers, Event Updates, Product News or Staff.</p><form id="group-form"><div class="field"><label>Group name</label><input name="name" required maxlength="100" placeholder="e.g. Existing customers"></div><button class="btn primary" type="submit">Create group</button></form><div class="notice mt-12">Contacts can belong to more than one group. A campaign only sends to the group selected for that campaign.</div></aside></div>`;
}
function renderCampaigns() {
  return `<div class="page-intro"><div><h2>Campaign manager</h2><p>Create, launch and review template-based WhatsApp campaigns.</p></div><div class="actions"><button class="btn primary" data-action="new-campaign">＋ Create campaign</button></div></div>
  <section class="panel"><div class="panel-header"><div><h3>Campaign history</h3><p>Recipient counts and latest delivery status</p></div><button class="btn small" data-action="refresh">↻ Refresh</button></div>
  ${state.campaigns.length?`<div class="table-wrap"><table><thead><tr><th>CAMPAIGN</th><th>GROUP</th><th>TEMPLATE</th><th>RECIPIENTS</th><th>SENT</th><th>DELIVERED</th><th>FAILED</th><th>STATUS</th><th></th></tr></thead><tbody>${state.campaigns.map(c=>`<tr><td><div class="primary-cell">${esc(c.name)}</div><div class="secondary-cell">${fmtDate(c.created_at)}</div></td><td>${esc(c.group_name)}</td><td><span class="code">${esc(c.template_name)}</span></td><td>${c.total}</td><td>${c.sent}</td><td>${c.delivered}</td><td>${c.failed}</td><td>${statusPill(c.status)}${c.dry_run?'<div class="secondary-cell">Dry run</div>':''}</td><td><button class="btn small" data-action="recipients" data-id="${c.id}">Details</button>${c.status==='draft'||c.status==='paused'?` <button class="btn small primary" data-action="send-campaign" data-id="${c.id}">Launch</button>`:''}</td></tr>`).join('')}</tbody></table></div>`:`<div class="empty-state"><div class="empty-icon">➤</div><strong>No campaigns created</strong><p>Make a campaign using a Meta-approved template and an opted-in contact group.</p><button class="btn primary" data-action="new-campaign">Create your first campaign</button></div>`}</section>`;
}
function renderTemplates() {
  return `<div class="page-intro"><div><h2>Approved message templates</h2><p>Live campaigns can use only templates approved in your WhatsApp Business Account.</p></div><div class="actions"><button class="btn" data-action="refresh-templates">↻ Refresh from Meta</button></div></div>
  <section class="panel"><div class="panel-header"><div><h3>Template library</h3><p>Template names and languages returned by Meta</p></div></div>
  ${state.templates.length?`<div class="table-wrap"><table><thead><tr><th>TEMPLATE NAME</th><th>LANGUAGE</th><th>CATEGORY</th><th>STATUS</th></tr></thead><tbody>${state.templates.map(t=>`<tr><td class="primary-cell">${esc(t.name)}</td><td>${esc(t.language)}</td><td>${esc(t.category||'—')}</td><td><span class="pill green">Approved</span></td></tr>`).join('')}</tbody></table></div>`:`<div class="empty-state"><div class="empty-icon">▧</div><strong>No template data loaded</strong><p>${esc(state.templateError||'Connect the Meta Cloud API and refresh to load approved templates. You can also enter an approved template name while creating a dry-run campaign.')}</p><button class="btn" data-action="refresh-templates">Load templates</button></div>`}</section>
  <div class="notice mt-12"><strong>Template placeholders.</strong> For positional body variables, set the field mapping in the order used in the approved template body. Example: a template with <span class="code">{{1}}</span> and <span class="code">{{2}}</span> can map to <span class="code">name,company</span>.</div>`;
}
function renderSettings() {
  const s=state.settings||{};
  return `<div class="page-intro"><div><h2>Workspace settings</h2><p>Configuration is stored in your server-side <span class="code">.env</span> file. Secrets are never displayed in this dashboard.</p></div></div>
  <div class="settings-grid"><section class="panel"><div class="panel-header"><div><h3>Connection status</h3><p>WhatsApp Business Cloud API</p></div><span class="pill ${s.meta_configured?'green':'orange'}">${s.meta_configured?'Connected credentials':'Not configured'}</span></div>
  <div class="settings-item"><strong>Meta access token</strong><span>${s.meta_configured?'Configured (hidden)':'Not configured'}</span></div><div class="settings-item"><strong>Phone Number ID</strong><span>${s.phone_number_configured?'Configured (hidden)':'Not configured'}</span></div><div class="settings-item"><strong>WhatsApp Business Account ID</strong><span>${s.templates_configured?'Configured (hidden)':'Not configured'}</span></div><div class="settings-item"><strong>Graph API version</strong><span>${esc(s.graph_api_version)}</span></div></section>
  <section class="panel"><div class="panel-header"><div><h3>Sending safety</h3><p>Current environment settings</p></div><span class="pill ${s.dry_run?'orange':'green'}">${s.dry_run?'Dry run':'Live sending'}</span></div><div class="settings-item"><strong>Dry-run mode</strong><span>${s.dry_run?'Enabled — messages are simulated':'Disabled — launches send real WhatsApp messages'}</span></div><div class="settings-item"><strong>Delay between messages</strong><span>${esc(s.send_delay_seconds)} seconds</span></div><div class="settings-item"><strong>Webhook verification</strong><span>Configured on server; set the same token in Meta webhook settings.</span></div></section></div>
  <section class="panel mt-12"><div class="panel-header"><div><h3>Connect your Meta account</h3><p>Update your local environment file and restart the container.</p></div></div><div class="settings-grid"><div class="notice"><strong>Required variables</strong><p class="no-margin"><span class="code">META_ACCESS_TOKEN</span><br><span class="code">META_PHONE_NUMBER_ID</span><br><span class="code">META_WABA_ID</span><br><span class="code">META_APP_SECRET</span> (recommended for webhook signatures)</p></div><div class="notice"><strong>Go-live checklist</strong><br>1. Complete Meta Business verification and Cloud API setup.<br>2. Add and approve message templates.<br>3. Configure a public HTTPS webhook URL ending in <span class="code">/webhook</span>.<br>4. Set <span class="code">DRY_RUN=false</span> only after testing.</div></div></section>`;
}
function render() {
  root.innerHTML = ({overview:renderOverview,contacts:renderContacts,groups:renderGroups,campaigns:renderCampaigns,templates:renderTemplates,settings:renderSettings})[state.view]();
}
function showModal(title, body) {
  const wrap=document.createElement('div'); wrap.className='modal-backdrop'; wrap.innerHTML=`<div class="modal" role="dialog" aria-modal="true"><div class="modal-head"><h3>${title}</h3><button class="icon-button" data-action="close-modal" aria-label="Close">✕</button></div>${body}</div>`;
  root.appendChild(wrap); wrap.addEventListener('click',e=>{if(e.target===wrap)wrap.remove()});
}
function campaignModal() {
  const groupOptions=state.groups.map(g=>`<option value="${g.id}">${esc(g.name)} (${g.eligible_count} opted-in)</option>`).join('');
  showModal('Create a campaign',`<form id="campaign-form"><div class="field"><label>Campaign name</label><input name="name" required maxlength="160" placeholder="November customer update"></div><div class="field"><label>Contact group</label><select name="group_id" required><option value="">Select a group</option>${groupOptions}</select><small>Only contacts with active recorded consent will be included.</small></div><div class="field"><label>Approved template name</label><input name="template_name" required placeholder="e.g. monthly_update"><small>Use the exact template name approved in WhatsApp Manager. Live mode verifies it with Meta.</small></div><div class="field"><label>Template language code</label><input name="language" required value="en_US" placeholder="en_US"></div><div class="field"><label>Body parameter contact fields (optional)</label><input name="body_fields" placeholder="name,company"><small>Comma-separated fields in the same order as {{1}}, {{2}} in the template. Use name, phone or CSV custom columns.</small></div><div class="notice"><strong>${state.settings.dry_run?'Dry-run is enabled.':'Live sending is enabled.'}</strong> ${state.settings.dry_run?'The campaign will be simulated.':'This campaign can send real messages after you launch it.'} Creating this campaign does not send anything.</div><div class="actions mt-12"><button class="btn primary" type="submit">Create campaign</button><button class="btn" type="button" data-action="close-modal">Cancel</button></div></form>`);
}
async function refresh() { await loadAll(); showToast('Workspace refreshed'); }
async function launchCampaign(id) {
  const campaign=state.campaigns.find(c=>c.id===Number(id));
  if(!campaign) return;
  const mode=campaign.dry_run?'DRY RUN (no messages will be sent)':'LIVE WHATSAPP SENDING';
  if(!confirm(`${mode}\n\nLaunch “${campaign.name}” to ${campaign.total} consented contact(s) in “${campaign.group_name}”?`)) return;
  try { await api(`/api/campaigns/${id}/send`,{method:'POST',body:JSON.stringify({confirm:true})}); showToast('Campaign queued'); await loadAll(); }
  catch(e){showToast(e.message,true)}
}
async function showRecipients(id) {
  try {
    const rows=await api(`/api/campaigns/${id}/recipients`);
    const body=rows.length?`<div class="recipient-list">${rows.map(r=>`<div class="recipient-row"><div><strong>${esc(r.name||'Unnamed')}</strong><div class="muted">${esc(r.phone)}</div>${r.error?`<div class="muted">${esc(r.error)}</div>`:''}</div><div>${statusPill(r.status)}</div></div>`).join('')}</div>`:'<p class="muted">No recipient records.</p>';
    showModal('Campaign recipients',body);
  } catch(e){showToast(e.message,true)}
}

root.addEventListener('click',async e=>{
  const view=e.target.closest('[data-view]'); if(view){setView(view.dataset.view);return;}
  const btn=e.target.closest('[data-action]'); if(!btn)return;
  const action=btn.dataset.action; const id=btn.dataset.id;
  if(action==='new-campaign') campaignModal();
  if(action==='refresh') {try{await refresh()}catch(e){showToast(e.message,true)}}
  if(action==='show-add-contact') document.getElementById('add-contact-form')?.classList.remove('hidden');
  if(action==='hide-add-contact') document.getElementById('add-contact-form')?.classList.add('hidden');
  if(action==='close-modal') document.querySelector('.modal-backdrop')?.remove();
  if(action==='send-campaign') launchCampaign(id);
  if(action==='recipients') showRecipients(id);
  if(action==='refresh-templates') {
    try {const data=await api('/api/templates'); state.templates=data.templates||[];state.templateError='';render();showToast(`Loaded ${state.templates.length} approved template(s)`)}
    catch(e){state.templateError=e.message;render();showToast(e.message,true)}
  }
  if(action==='opt-out') {
    if(!confirm('Opt this contact out of future campaigns?'))return;
    try{await api(`/api/contacts/${id}/opt-out`,{method:'POST',body:'{}'});await loadAll();showToast('Contact opted out')}catch(e){showToast(e.message,true)}
  }
});

root.addEventListener('submit',async e=>{
  e.preventDefault(); const form=e.target;
  try {
    if(form.id==='group-form') {
      const data=Object.fromEntries(new FormData(form)); await api('/api/groups',{method:'POST',body:JSON.stringify(data)}); form.reset(); await loadAll(); showToast('Group created');
    }
    if(form.id==='contact-form') {
      const fd=new FormData(form); const payload=Object.fromEntries(fd); payload.opted_in=fd.get('opted_in')==='on'; payload.group_id=payload.group_id ? Number(payload.group_id) : null;
      await api('/api/contacts',{method:'POST',body:JSON.stringify(payload)}); form.reset(); document.getElementById('add-contact-form').classList.add('hidden'); await loadAll(); showToast('Contact added');
    }
    if(form.id==='import-form') {
      const fd=new FormData(form); const result=await api('/api/contacts/import',{method:'POST',body:fd}); form.reset(); await loadAll();
      showToast(`Imported ${result.imported}; skipped ${result.skipped}; duplicates ${result.duplicates}`);
      if(result.errors?.length) alert(result.errors.join('\n'));
    }
    if(form.id==='campaign-form') {
      const fd=new FormData(form); const data=Object.fromEntries(fd); data.group_id=Number(data.group_id); data.body_fields=(data.body_fields||'').split(',').map(s=>s.trim()).filter(Boolean); delete data.body_fields_text;
      const result=await api('/api/campaigns',{method:'POST',body:JSON.stringify(data)}); document.querySelector('.modal-backdrop')?.remove(); await loadAll(); setView('campaigns'); showToast(`Campaign created with ${result.total} eligible recipient(s). Launch it when ready.`);
    }
  } catch(err) { showToast(err.message,true); }
});

loadAll().catch(err=>{root.innerHTML=`<section class="panel empty-state"><strong>Could not load workspace</strong><p>${esc(err.message)}. Check the server logs and configuration.</p></section>`;});
