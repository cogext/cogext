"""Public Audit Receipt API — V2.0.

POST /commitments/{id}/receipt  → generate & store receipt token (auth required)
GET  /audit/{token}             → public, no auth — returns commitment snapshot
"""
from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.auth import Account, get_current_account
from app.core.receipt import generate_receipt_token, verify_receipt_token
from app.db.connection import get_supabase

logger = logging.getLogger(__name__)
router = APIRouter()


class ReceiptResponse(BaseModel):
    receipt_token: str
    audit_url: str


class AuditSnapshot(BaseModel):
    commitment_id: str
    promise_text: str
    status: str
    confidence: float
    created_at: str | None
    deadline: str | None
    agent_id: str
    receipt_valid: bool


# ── Protected: generate receipt ──────────────────────────────────────────────

@router.post("/commitments/{commitment_id}/receipt", response_model=ReceiptResponse)
async def create_receipt(
    commitment_id: UUID,
    account: Account = Depends(get_current_account),
) -> ReceiptResponse:
    sb = get_supabase()

    row = await (
        sb.table("commitments")
        .select("id,user_id,promise_text,created_at,receipt_token")
        .eq("id", str(commitment_id))
        .eq("user_id", account.account_id)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Commitment not found")

    data = row.data

    # Return existing token if already generated
    if data.get("receipt_token"):
        token = data["receipt_token"]
    else:
        token = generate_receipt_token(
            commitment_id=data["id"],
            user_id=data["user_id"],
            promise_text=data["promise_text"],
            created_at=str(data.get("created_at", "")),
        )
        await sb.table("commitments").update({"receipt_token": token}).eq("id", data["id"]).execute()

    return ReceiptResponse(
        receipt_token=token,
        audit_url=f"https://api.cogextai.com/api/v1/receipt/{token}",
    )


# ── Public: verify + display receipt ────────────────────────────────────────

@router.get("/audit/{token}", response_model=AuditSnapshot)
async def get_audit(token: str) -> AuditSnapshot:
    sb = get_supabase()

    row = await (
        sb.table("commitments")
        .select("id,user_id,promise_text,status,confidence,created_at,due_condition,source_agent_id,receipt_token")
        .eq("receipt_token", token)
        .maybe_single()
        .execute()
    )
    if not row or not row.data:
        raise HTTPException(status_code=404, detail="Receipt not found or invalid")

    data = row.data
    due = data.get("due_condition") or {}
    deadline = due.get("deadline") if isinstance(due, dict) else None

    valid = verify_receipt_token(
        token=token,
        commitment_id=data["id"],
        user_id=data["user_id"],
        promise_text=data["promise_text"],
        created_at=str(data.get("created_at", "")),
    )

    return AuditSnapshot(
        commitment_id=data["id"],
        promise_text=data["promise_text"],
        status=data["status"],
        confidence=data["confidence"],
        created_at=data.get("created_at"),
        deadline=deadline,
        agent_id=data.get("source_agent_id", ""),
        receipt_valid=valid,
    )


# ── Public: serve receipt HTML page ─────────────────────────────────────────

from fastapi.responses import HTMLResponse as _HTMLResponse

@router.get("/receipt/{token}", response_class=_HTMLResponse, include_in_schema=False)
async def receipt_page(token: str) -> _HTMLResponse:
    """Serve the tamper-evident receipt page for sharing."""
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>COGEXT — Commitment Audit Receipt</title>
<style>
  :root{--bg:#0a0d14;--surface:#111620;--border:#1e2535;--text:#e2e8f4;--muted:#6b7a99;--accent:#4f8ef7;--green:#22c55e;--red:#ef4444;--yellow:#f59e0b;--radius:10px;--font:'Inter',system-ui,sans-serif}
  *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
  body{font-family:var(--font);background:var(--bg);color:var(--text);min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:24px 16px}
  a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
  .wordmark{font-size:13px;letter-spacing:.15em;color:var(--muted);text-transform:uppercase;margin-bottom:32px}.wordmark span{color:var(--accent)}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:32px;width:100%;max-width:580px}
  .card-header{display:flex;align-items:center;gap:12px;margin-bottom:28px}
  .badge-icon{width:40px;height:40px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0}
  .badge-icon.valid{background:rgba(34,197,94,.15)}.badge-icon.invalid{background:rgba(239,68,68,.15)}.badge-icon.loading{background:rgba(79,142,247,.1);animation:pulse 1.5s infinite}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.5}}
  .card-title{font-size:18px;font-weight:600}.card-sub{font-size:13px;color:var(--muted);margin-top:2px}
  .promise-box{background:rgba(79,142,247,.07);border:1px solid rgba(79,142,247,.2);border-radius:8px;padding:16px;font-size:15px;line-height:1.6;margin-bottom:24px;font-style:italic;color:#c5d3f0}
  .promise-box::before{content:'“'}.promise-box::after{content:'”'}
  .fields{display:flex;flex-direction:column;gap:12px}
  .field{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;padding-bottom:12px;border-bottom:1px solid var(--border)}.field:last-child{border-bottom:none;padding-bottom:0}
  .field-label{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;flex-shrink:0;padding-top:2px}
  .field-value{font-size:13px;font-weight:500;text-align:right}
  .status-chip{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em}
  .status-fulfilled{background:rgba(34,197,94,.15);color:#4ade80}.status-open{background:rgba(79,142,247,.15);color:#7eb3ff}.status-failed{background:rgba(239,68,68,.15);color:#f87171}.status-overdue{background:rgba(245,158,11,.15);color:#fbbf24}.status-other{background:rgba(107,122,153,.15);color:#8fa0c0}
  .conf-bar-wrap{width:80px;background:var(--border);border-radius:4px;height:6px;overflow:hidden;display:inline-block;vertical-align:middle;margin-left:8px}
  .conf-bar{height:100%;background:var(--accent);border-radius:4px}
  .token-box{margin-top:24px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px 14px;font-family:'SF Mono','Fira Code',monospace;font-size:11px;color:var(--muted);word-break:break-all}
  .token-label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
  .tamper-notice{margin-top:16px;font-size:12px;color:var(--muted);text-align:center}
  .footer{margin-top:24px;font-size:12px;color:var(--muted);text-align:center}
</style>
</head>
<body>
<div class="wordmark"><span>COG</span>EXT — Commitment Audit</div>
<div class="card" id="card">
  <div class="card-header">
    <div class="badge-icon loading" id="badge">⏳</div>
    <div><div class="card-title" id="card-title">Loading receipt…</div><div class="card-sub" id="card-sub">Verifying token integrity</div></div>
  </div>
  <div id="card-body"></div>
</div>
<div class="footer">Powered by <a href="https://cogextai.com">COGEXT</a> — AI commitment tracking infrastructure</div>
<script>
const TOKEN=__TOKEN_PLACEHOLDER__;
const API='https://api.cogextai.com/api/v1';
function statusChip(s){const cls={fulfilled:'fulfilled',open:'open',failed:'failed',overdue:'overdue'}[s]||'other';return`<span class="status-chip status-${cls}">${s}</span>`}
function fmtDate(d){if(!d)return'—';try{return new Date(d).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'short'})}catch{return d}}
function confBar(c){const p=Math.round(c*100);return`${p}% <span class="conf-bar-wrap"><span class="conf-bar" style="width:${p}%"></span></span>`}
function escHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
async function load(){
  try{
    const res=await fetch(`${API}/audit/${encodeURIComponent(TOKEN)}`);
    if(res.status===404)return showError('Receipt not found.','This token may be invalid or the commitment was deleted.');
    if(!res.ok)return showError(`Server error (${res.status})`,'Please try again later.');
    const d=await res.json();render(d,TOKEN);
  }catch(e){showError('Network error','Could not reach COGEXT API. '+e.message)}
}
function render(d,token){
  const valid=d.receipt_valid;
  const badge=document.getElementById('badge');
  badge.className=`badge-icon ${valid?'valid':'invalid'}`;
  badge.textContent=valid?'✓':'✗';
  document.getElementById('card-title').textContent=valid?'Verified Commitment Receipt':'Receipt Integrity Check Failed';
  document.getElementById('card-sub').textContent=valid?'This commitment record is authentic and has not been tampered with.':'The HMAC signature does not match — data may have been altered.';
  document.getElementById('card-body').innerHTML=`
    <div class="promise-box">${escHtml(d.promise_text)}</div>
    <div class="fields">
      <div class="field"><span class="field-label">Status</span><span class="field-value">${statusChip(d.status)}</span></div>
      <div class="field"><span class="field-label">Confidence</span><span class="field-value">${confBar(d.confidence)}</span></div>
      <div class="field"><span class="field-label">Committed at</span><span class="field-value">${fmtDate(d.created_at)}</span></div>
      <div class="field"><span class="field-label">Deadline</span><span class="field-value">${fmtDate(d.deadline)}</span></div>
      <div class="field"><span class="field-label">Commitment ID</span><span class="field-value" style="font-family:monospace;font-size:11px;color:var(--muted)">${escHtml(d.commitment_id)}</span></div>
    </div>
    <div class="token-label" style="margin-top:20px">Receipt token</div>
    <div class="token-box">${escHtml(token)}</div>
    <div class="tamper-notice">${valid?'🔒 HMAC-SHA256 signature verified — record matches original commitment':'⚠️ Signature mismatch — do not rely on this receipt'}</div>`;
}
function showError(title,detail){
  document.getElementById('badge').className='badge-icon invalid';
  document.getElementById('badge').textContent='✗';
  document.getElementById('card-title').textContent=title;
  document.getElementById('card-sub').textContent='';
  document.getElementById('card-body').innerHTML=`<div style="text-align:center;padding:32px 0"><p style="font-size:14px;color:var(--muted)">${escHtml(detail)}</p></div>`;
}
load();
</script>
</body>
</html>"""
    # Inject the token server-side so no query param needed
    html = html.replace("__TOKEN_PLACEHOLDER__", f'"{token}"')
    return _HTMLResponse(content=html)
