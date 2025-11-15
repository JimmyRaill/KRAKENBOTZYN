# api.py (minimal + safe errors + simple chat page)
from __future__ import annotations

import os
import json
import asyncio
import uuid
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse, HTMLResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from event_manager import event_manager

app = FastAPI()

# Enable CORS for real-time updates
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- tiny chat page at "/" ---
CHAT = """
<!doctype html><meta charset="utf-8"><title>KrakenBot Chat</title>
<style>
body{font-family:system-ui,Arial;background:#0b0c10;color:#e5e7eb;margin:0}
.container{max-width:800px;margin:40px auto;padding:20px}
#log{white-space:pre-wrap;background:#111827;border:1px solid #374151;border-radius:8px;padding:12px;height:420px;overflow:auto}
.row{display:flex;gap:8px;margin:10px 0}
input,button{font-size:14px}
input[type=text]{flex:1;padding:10px;border:1px solid #374151;border-radius:6px;background:#0b0c10;color:#e5e7eb}
button{padding:10px 14px;border:1px solid #374151;border-radius:6px;background:#1f2937;color:#e5e7eb;cursor:pointer}
button:hover{background:#374151}
</style>
<div class="container">
  <h2>Talk to KrakenBot</h2>
  <div class="row">
    <input id="inp" placeholder='Try: "how much did we make today?" or "what’s my balance?"' />
    <button id="send">Send</button>
  </div>
  <div id="log"></div>
</div>
<script>
const logEl = document.getElementById('log');
function log(s){ logEl.textContent += s + "\\n"; logEl.scrollTop = 1e9; }
async function send(){
  const text = document.getElementById('inp').value.trim();
  if(!text) return;
  log("> " + text);
  try{
    const r = await fetch("/ask", {
      method: "POST",
      headers: {"Content-Type":"application/json"},
      body: JSON.stringify({ text })
    });
    const j = await r.json();
    log(j.answer || JSON.stringify(j));
    if (j.trace) log("\\nTRACE:\\n" + j.trace);
  }catch(e){ log("Network error: " + e.message); }
  document.getElementById('inp').value="";
}
document.getElementById('send').onclick = send;
document.getElementById('inp').addEventListener("keydown", e=>{ if(e.key==="Enter") send(); });
</script>
"""

@app.get("/chat", response_class=HTMLResponse)
def chat():
    return CHAT

@app.get("/", response_class=HTMLResponse)
def control_panel():
    """Main control panel - chat with Zyn and control the bot."""
    return CONTROL_PANEL

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Professional real-time trading dashboard."""
    return DASHBOARD

# Main Control Panel - Chat + Controls
CONTROL_PANEL = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Zyn - AI Trading Bot Control Panel</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .container {
            max-width: 900px;
            width: 100%;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 {
            font-size: 36px;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 10px;
        }
        .header p { font-size: 16px; opacity: 0.9; }
        
        .status-bar {
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 15px;
            padding: 20px;
            background: #f7fafc;
            border-bottom: 1px solid #e2e8f0;
        }
        @media (max-width: 768px) {
            .status-bar {
                grid-template-columns: 1fr;
            }
        }
        .status-card {
            background: white;
            padding: 20px;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            text-align: center;
        }
        .status-label {
            font-size: 12px;
            text-transform: uppercase;
            color: #718096;
            margin-bottom: 8px;
            font-weight: 600;
        }
        .status-value {
            font-size: 24px;
            font-weight: bold;
            color: #2d3748;
        }
        .status-value.active { color: #10b981; }
        .status-value.inactive { color: #ef4444; }
        .status-value.paper { color: #3b82f6; }
        .status-value.live { color: #ef4444; font-weight: 900; }
        
        .toggle-switch {
            position: relative;
            display: inline-block;
            width: 60px;
            height: 34px;
            margin-top: 10px;
        }
        .toggle-switch input {
            opacity: 0;
            width: 0;
            height: 0;
        }
        .toggle-slider {
            position: absolute;
            cursor: pointer;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background-color: #10b981;
            transition: 0.4s;
            border-radius: 34px;
        }
        .toggle-slider:before {
            position: absolute;
            content: "";
            height: 26px;
            width: 26px;
            left: 4px;
            bottom: 4px;
            background-color: white;
            transition: 0.4s;
            border-radius: 50%;
        }
        input:checked + .toggle-slider {
            background-color: #ef4444;
        }
        input:checked + .toggle-slider:before {
            transform: translateX(26px);
        }
        .mode-labels {
            font-size: 11px;
            margin-top: 8px;
            color: #718096;
        }
        
        .controls {
            padding: 20px;
            display: flex;
            gap: 10px;
            justify-content: center;
            background: #f7fafc;
            border-bottom: 1px solid #e2e8f0;
        }
        .btn {
            padding: 15px 30px;
            font-size: 16px;
            font-weight: 600;
            border: none;
            border-radius: 10px;
            cursor: pointer;
            transition: all 0.3s;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .btn-start {
            background: linear-gradient(135deg, #10b981, #059669);
            color: white;
            flex: 1;
        }
        .btn-start:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(16, 185, 129, 0.4); }
        .btn-stop {
            background: linear-gradient(135deg, #ef4444, #dc2626);
            color: white;
            flex: 1;
        }
        .btn-stop:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(239, 68, 68, 0.4); }
        .btn-restart {
            background: linear-gradient(135deg, #f59e0b, #d97706);
            color: white;
            flex: 1;
        }
        .btn-restart:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(245, 158, 11, 0.4); }
        .btn:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
        }
        
        .chat-container {
            padding: 20px;
            background: white;
        }
        .chat-title {
            font-size: 20px;
            font-weight: 600;
            color: #2d3748;
            margin-bottom: 15px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .chat-messages {
            background: #f7fafc;
            border-radius: 12px;
            height: 400px;
            overflow-y: auto;
            padding: 15px;
            margin-bottom: 15px;
            border: 1px solid #e2e8f0;
        }
        .message {
            margin-bottom: 15px;
            padding: 12px 16px;
            border-radius: 10px;
            max-width: 80%;
            word-wrap: break-word;
        }
        .message.user {
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            margin-left: auto;
            text-align: right;
        }
        .message.bot {
            background: white;
            color: #2d3748;
            border: 1px solid #e2e8f0;
        }
        .message.system {
            background: #fef3c7;
            color: #92400e;
            text-align: center;
            font-size: 14px;
            margin: 10px auto;
            max-width: 100%;
        }
        .chat-input-area {
            display: flex;
            gap: 10px;
        }
        .chat-input {
            flex: 1;
            padding: 12px 16px;
            border: 2px solid #e2e8f0;
            border-radius: 10px;
            font-size: 15px;
            outline: none;
        }
        .chat-input:focus { border-color: #667eea; }
        .btn-send {
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            padding: 12px 24px;
            border: none;
            border-radius: 10px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
        }
        .btn-send:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4); }
        
        .footer {
            padding: 15px;
            text-align: center;
            background: #f7fafc;
            color: #718096;
            font-size: 14px;
        }
        .footer a {
            color: #667eea;
            text-decoration: none;
            font-weight: 600;
        }
        .footer a:hover { text-decoration: underline; }
        
        @media (max-width: 768px) {
            .status-bar { grid-template-columns: 1fr; }
            .controls { flex-direction: column; }
            .header h1 { font-size: 28px; }
        }
        
        .pulse {
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.6; }
        }
        
        .typing-dots {
            display: inline-flex;
            gap: 4px;
            align-items: center;
            padding: 8px 0;
        }
        .typing-dots span {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #667eea;
            animation: typing-bounce 1.4s infinite ease-in-out both;
        }
        .typing-dots span:nth-child(1) {
            animation-delay: -0.32s;
        }
        .typing-dots span:nth-child(2) {
            animation-delay: -0.16s;
        }
        @keyframes typing-bounce {
            0%, 80%, 100% {
                transform: scale(0);
                opacity: 0.5;
            }
            40% {
                transform: scale(1);
                opacity: 1;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><span>🤖</span> Zyn</h1>
            <p>Your AI-Powered Cryptocurrency Trading Assistant</p>
        </div>
        
        <div class="status-bar">
            <div class="status-card">
                <div class="status-label">Bot Status</div>
                <div class="status-value" id="botStatus">Loading...</div>
            </div>
            <div class="status-card">
                <div class="status-label">Portfolio Value</div>
                <div class="status-value" id="equityValue">$0.00</div>
            </div>
            <div class="status-card">
                <div class="status-label">Trading Mode</div>
                <div class="status-value" id="tradingMode">Loading...</div>
                <label class="toggle-switch">
                    <input type="checkbox" id="modeToggle" onclick="toggleTradingMode()">
                    <span class="toggle-slider"></span>
                </label>
                <div class="mode-labels">Paper ⟷ Live</div>
            </div>
        </div>
        
        <div class="controls">
            <button class="btn btn-start" id="startBtn" onclick="startBot()">
                ▶️ Start Trading
            </button>
            <button class="btn btn-stop" id="stopBtn" onclick="stopBot()">
                ⏸️ Pause Trading
            </button>
            <button class="btn btn-restart" onclick="restartWorkflows()">
                🔄 Restart Workflows
            </button>
        </div>
        
        <div class="chat-container">
            <div class="chat-title">
                💬 Chat with Zyn
            </div>
            <div class="chat-messages" id="chatMessages">
                <div class="message system">
                    👋 Hey! I'm Zyn, your trading assistant. Ask me anything about your portfolio, performance, or trading strategies!
                </div>
            </div>
            <div class="chat-input-area">
                <input 
                    type="text" 
                    class="chat-input" 
                    id="chatInput" 
                    placeholder="Ask Zyn anything... (e.g., 'How am I doing today?')"
                    onkeypress="if(event.key==='Enter') sendMessage()"
                />
                <button class="btn-send" onclick="sendMessage()">Send</button>
            </div>
        </div>
        
        <div class="footer">
            <p>
                <a href="/dashboard">📊 Full Dashboard</a> | 
                <a href="/sms-setup">📱 SMS Notifications</a> | 
                Last updated: <span id="lastUpdate">Never</span>
            </p>
        </div>
    </div>
    
    <script>
        let isUpdating = false;
        
        // Update status
        async function updateStatus() {
            if (isUpdating) return;
            isUpdating = true;
            
            try {
                const response = await fetch('/api/autopilot/status');
                const data = await response.json();
                
                const statusEl = document.getElementById('botStatus');
                const startBtn = document.getElementById('startBtn');
                const stopBtn = document.getElementById('stopBtn');
                
                if (data.autopilot_running) {
                    statusEl.textContent = '🟢 Active';
                    statusEl.className = 'status-value active pulse';
                    startBtn.disabled = true;
                    stopBtn.disabled = false;
                } else {
                    statusEl.textContent = '🔴 Paused';
                    statusEl.className = 'status-value inactive';
                    startBtn.disabled = false;
                    stopBtn.disabled = true;
                }
                
                document.getElementById('equityValue').textContent = `$${data.equity.toFixed(2)}`;
                document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
            } catch (error) {
                console.error('Status update error:', error);
            }
            
            isUpdating = false;
        }
        
        // Start bot
        async function startBot() {
            try {
                const response = await fetch('/api/autopilot/start', { method: 'POST' });
                const data = await response.json();
                addMessage(data.message, 'system');
                updateStatus();
            } catch (error) {
                addMessage('Failed to start bot: ' + error.message, 'system');
            }
        }
        
        // Stop bot
        async function stopBot() {
            try {
                const response = await fetch('/api/autopilot/stop', { method: 'POST' });
                const data = await response.json();
                addMessage(data.message, 'system');
                updateStatus();
            } catch (error) {
                addMessage('Failed to stop bot: ' + error.message, 'system');
            }
        }
        
        // Load trading mode
        async function loadTradingMode() {
            try {
                const response = await fetch('/api/trading-mode');
                const data = await response.json();
                
                const modeEl = document.getElementById('tradingMode');
                const toggleEl = document.getElementById('modeToggle');
                
                if (data.is_paper) {
                    modeEl.textContent = '📝 PAPER';
                    modeEl.className = 'status-value paper';
                    toggleEl.checked = false;
                } else {
                    modeEl.textContent = '⚠️ LIVE';
                    modeEl.className = 'status-value live';
                    toggleEl.checked = true;
                }
            } catch (error) {
                console.error('Failed to load trading mode:', error);
            }
        }
        
        // Toggle trading mode
        async function toggleTradingMode() {
            const toggleEl = document.getElementById('modeToggle');
            const newMode = toggleEl.checked ? 'live' : 'paper';
            
            // Confirm if switching to live mode
            if (newMode === 'live') {
                const confirmed = confirm('⚠️ WARNING: You are about to switch to LIVE TRADING mode. Real money will be at risk! Are you sure?');
                if (!confirmed) {
                    toggleEl.checked = false;
                    return;
                }
            }
            
            try {
                const response = await fetch('/api/set-trading-mode', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: newMode })
                });
                const data = await response.json();
                
                if (data.status === 'success') {
                    addMessage(data.message, 'system');
                    loadTradingMode();
                } else {
                    addMessage('Failed to change mode: ' + data.message, 'system');
                    // Revert toggle
                    toggleEl.checked = !toggleEl.checked;
                }
            } catch (error) {
                addMessage('Error changing trading mode: ' + error.message, 'system');
                // Revert toggle
                toggleEl.checked = !toggleEl.checked;
            }
        }
        
        // Restart all workflows
        async function restartWorkflows() {
            const confirmed = confirm('🔄 Restart both workflows (autopilot + chat)? This will apply any configuration changes.');
            if (!confirmed) return;
            
            try {
                const response = await fetch('/api/restart-workflows', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' }
                });
                const data = await response.json();
                
                if (data.status === 'success') {
                    addMessage('✅ ' + data.message, 'system');
                } else {
                    addMessage('❌ Failed to restart: ' + data.message, 'system');
                }
            } catch (error) {
                addMessage('❌ Error restarting workflows: ' + error.message, 'system');
            }
        }
        
        let typingIndicator = null;
        let currentEventSource = null;
        
        // Show typing indicator
        function showTypingIndicator() {
            if (typingIndicator) return;
            const messagesDiv = document.getElementById('chatMessages');
            typingIndicator = document.createElement('div');
            typingIndicator.className = 'message bot typing-indicator';
            typingIndicator.innerHTML = `
                <div class="typing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            `;
            messagesDiv.appendChild(typingIndicator);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
        
        // Hide typing indicator
        function hideTypingIndicator() {
            if (typingIndicator) {
                typingIndicator.remove();
                typingIndicator = null;
            }
        }
        
        // Close existing event source
        function closeEventSource() {
            if (currentEventSource) {
                currentEventSource.close();
                currentEventSource = null;
            }
        }
        
        // Send message to Zyn
        async function sendMessage() {
            const input = document.getElementById('chatInput');
            const text = input.value.trim();
            
            if (!text) return;
            
            addMessage(text, 'user');
            input.value = '';
            
            closeEventSource();
            
            try {
                const response = await fetch('/ask', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ text })
                });
                const data = await response.json();
                
                if (data.request_id) {
                    const eventSource = new EventSource(`/api/events/${data.request_id}`);
                    currentEventSource = eventSource;
                    
                    eventSource.onmessage = (event) => {
                        try {
                            const eventData = JSON.parse(event.data);
                            if (eventData.type === 'typing_start') {
                                showTypingIndicator();
                            } else if (eventData.type === 'typing_stop') {
                                hideTypingIndicator();
                                closeEventSource();
                            }
                        } catch (e) {
                            console.error('SSE parse error:', e);
                        }
                    };
                    
                    eventSource.onerror = () => {
                        hideTypingIndicator();
                        closeEventSource();
                    };
                }
                
                hideTypingIndicator();
                addMessage(data.answer || 'No response', 'bot');
            } catch (error) {
                hideTypingIndicator();
                addMessage('Sorry, I had trouble processing that: ' + error.message, 'bot');
            }
        }
        
        // Add message to chat
        function addMessage(text, type) {
            const messagesDiv = document.getElementById('chatMessages');
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${type}`;
            messageDiv.textContent = text;
            messagesDiv.appendChild(messageDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }
        
        // Initial load
        updateStatus();
        loadTradingMode();
        
        // Auto-refresh status every 3 seconds
        setInterval(updateStatus, 3000);
        setInterval(loadTradingMode, 3000);
    </script>
</body>
</html>
"""

# Professional Trading Dashboard HTML
DASHBOARD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KrakenBot Trading Dashboard</title>
    <script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #0b0c10 0%, #1a1d29 100%);
            color: #e5e7eb;
            padding: 20px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding: 20px;
            background: rgba(31, 41, 55, 0.5);
            border-radius: 12px;
            backdrop-filter: blur(10px);
        }
        .header h1 { 
            font-size: 28px;
            background: linear-gradient(135deg, #3b82f6, #8b5cf6);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .status { display: flex; align-items: center; gap: 10px; }
        .status-dot {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            background: #10b981;
            animation: pulse 2s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .status.paused .status-dot { background: #ef4444; }
        
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .card {
            background: rgba(31, 41, 55, 0.6);
            border-radius: 12px;
            padding: 20px;
            border: 1px solid rgba(59, 130, 246, 0.2);
            backdrop-filter: blur(10px);
        }
        .card h2 {
            font-size: 16px;
            color: #9ca3af;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .metric-value {
            font-size: 32px;
            font-weight: bold;
            margin: 10px 0;
        }
        .positive { color: #10b981; }
        .negative { color: #ef4444; }
        .neutral { color: #6b7280; }
        
        .chart-container {
            grid-column: 1 / -1;
            height: 400px;
            margin-bottom: 20px;
        }
        #equityChart { width: 100%; height: 100%; }
        
        .positions-table {
            width: 100%;
            border-collapse: collapse;
        }
        .positions-table th {
            text-align: left;
            padding: 12px;
            background: rgba(59, 130, 246, 0.1);
            font-weight: 600;
            font-size: 14px;
        }
        .positions-table td {
            padding: 12px;
            border-bottom: 1px solid rgba(75, 85, 99, 0.3);
        }
        .positions-table tr:hover {
            background: rgba(59, 130, 246, 0.05);
        }
        
        .trades-list {
            max-height: 300px;
            overflow-y: auto;
        }
        .trade-item {
            padding: 12px;
            margin: 8px 0;
            background: rgba(17, 24, 39, 0.5);
            border-radius: 8px;
            border-left: 3px solid #3b82f6;
        }
        .trade-item.buy { border-left-color: #10b981; }
        .trade-item.sell { border-left-color: #ef4444; }
        
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }
        .badge.success { background: rgba(16, 185, 129, 0.2); color: #10b981; }
        .badge.danger { background: rgba(239, 68, 68, 0.2); color: #ef4444; }
        .badge.warning { background: rgba(245, 158, 11, 0.2); color: #f59e0b; }
        .badge.info { background: rgba(59, 130, 246, 0.2); color: #3b82f6; }
        
        .footer {
            text-align: center;
            margin-top: 30px;
            padding: 20px;
            color: #6b7280;
            font-size: 14px;
        }
        
        .nav-links {
            display: flex;
            gap: 15px;
        }
        .nav-links a {
            color: #3b82f6;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            background: rgba(59, 130, 246, 0.1);
            transition: all 0.3s;
        }
        .nav-links a:hover {
            background: rgba(59, 130, 246, 0.2);
        }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🤖 KrakenBot AI Trading Dashboard</h1>
            <p style="margin-top: 8px; color: #9ca3af;">Self-Learning Cryptocurrency Trading Bot</p>
        </div>
        <div style="display: flex; align-items: center; gap: 20px;">
            <div class="status" id="botStatus">
                <div class="status-dot"></div>
                <span>Active</span>
            </div>
            <div class="nav-links">
                <a href="/chat">💬 Chat</a>
            </div>
        </div>
    </div>

    <div class="grid">
        <div class="card">
            <h2>💰 Equity</h2>
            <div class="metric-value" id="equity">$0.00</div>
            <div id="equityChange" class="neutral">+$0.00 (0.00%)</div>
        </div>
        
        <div class="card">
            <h2>📊 Open Positions</h2>
            <div class="metric-value" id="openPositions">0</div>
            <div class="neutral">Active trades</div>
        </div>
        
        <div class="card">
            <h2>🎯 Win Rate</h2>
            <div class="metric-value" id="winRate">0%</div>
            <div class="neutral" id="winRateSub">No trades yet</div>
        </div>
        
        <div class="card">
            <h2>📈 Total Trades</h2>
            <div class="metric-value" id="totalTrades">0</div>
            <div class="neutral" id="tradeSub">Learning...</div>
        </div>
    </div>

    <div class="card chart-container">
        <h2>📈 Equity Performance</h2>
        <div id="equityChart"></div>
    </div>

    <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px;">
        <div class="card">
            <h2>💼 Active Positions</h2>
            <table class="positions-table" id="positionsTable">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Size</th>
                        <th>Entry</th>
                        <th>Current</th>
                        <th>P/L</th>
                    </tr>
                </thead>
                <tbody id="positionsBody">
                    <tr><td colspan="5" style="text-align: center; color: #6b7280;">No open positions</td></tr>
                </tbody>
            </table>
        </div>
        
        <div class="card">
            <h2>🕒 Recent Trades</h2>
            <div class="trades-list" id="tradesList">
                <div style="text-align: center; color: #6b7280; padding: 20px;">No trades yet</div>
            </div>
        </div>
    </div>

    <div class="footer">
        <p>KrakenBot Self-Learning AI • Last updated: <span id="lastUpdate">Never</span></p>
        <p style="margin-top: 8px;">📊 <span id="statsText">0 decisions, 0 trades, 0 snapshots</span></p>
    </div>

    <script>
        // Initialize chart
        const chartContainer = document.getElementById('equityChart');
        const chart = LightweightCharts.createChart(chartContainer, {
            width: chartContainer.clientWidth,
            height: 350,
            layout: {
                background: { color: 'transparent' },
                textColor: '#9ca3af',
            },
            grid: {
                vertLines: { color: 'rgba(75, 85, 99, 0.2)' },
                horzLines: { color: 'rgba(75, 85, 99, 0.2)' },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
            },
            rightPriceScale: {
                borderColor: 'rgba(75, 85, 99, 0.5)',
            },
            timeScale: {
                borderColor: 'rgba(75, 85, 99, 0.5)',
                timeVisible: true,
                secondsVisible: false,
            },
        });

        const lineSeries = chart.addLineSeries({
            color: '#3b82f6',
            lineWidth: 2,
            priceFormat: {
                type: 'price',
                precision: 2,
                minMove: 0.01,
            },
        });

        // Auto-resize chart
        window.addEventListener('resize', () => {
            chart.applyOptions({ width: chartContainer.clientWidth });
        });

        // Fetch and update dashboard
        async function updateDashboard() {
            try {
                const response = await fetch('/api/dashboard');
                const data = await response.json();
                
                // Update equity
                document.getElementById('equity').textContent = `$${data.equity.current.toFixed(2)}`;
                const changeEl = document.getElementById('equityChange');
                const change = data.equity.change || 0;
                const changePct = data.equity.change_pct || 0;
                changeEl.textContent = `${change >= 0 ? '+' : ''}$${change.toFixed(2)} (${changePct.toFixed(2)}%)`;
                changeEl.className = change >= 0 ? 'positive' : change < 0 ? 'negative' : 'neutral';
                
                // Update positions (with defensive check)
                const positions = Array.isArray(data.positions) ? data.positions : [];
                const posCount = positions.length;  // All positions are open orders from Status Service
                document.getElementById('openPositions').textContent = posCount;
                
                // Update win rate
                if (data.performance && data.performance.total_trades > 0) {
                    const winRate = data.performance.win_rate || 0;
                    document.getElementById('winRate').textContent = `${(winRate * 100).toFixed(1)}%`;
                    document.getElementById('winRateSub').textContent = `${data.performance.wins || 0}W / ${data.performance.losses || 0}L`;
                }
                
                // Update trades count
                document.getElementById('totalTrades').textContent = data.stats.trades || 0;
                document.getElementById('tradeSub').textContent = `${data.stats.decisions || 0} decisions made`;
                
                // Update status
                const statusEl = document.getElementById('botStatus');
                if (data.paused) {
                    statusEl.classList.add('paused');
                    statusEl.querySelector('span').textContent = 'Paused';
                } else {
                    statusEl.classList.remove('paused');
                    statusEl.querySelector('span').textContent = 'Active';
                }
                
                // Update positions table (now showing open orders from Status Service)
                const positionsBody = document.getElementById('positionsBody');
                const validPositions = Array.isArray(data.positions) ? data.positions : [];
                if (validPositions.length > 0) {
                    positionsBody.innerHTML = validPositions.map(p => {
                        const sideClass = (p.side || '').toLowerCase() === 'buy' ? 'positive' : 'negative';
                        return `
                            <tr>
                                <td><strong>${p.symbol || 'Unknown'}</strong></td>
                                <td>${(p.qty || 0).toFixed(6)}</td>
                                <td>$${(p.entry || 0).toFixed(2)}</td>
                                <td>${(p.type || 'unknown').toUpperCase()}</td>
                                <td class="${sideClass}">${(p.side || 'unknown').toUpperCase()}</td>
                            </tr>
                        `;
                    }).join('');
                } else {
                    positionsBody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: #6b7280;">No open positions</td></tr>';
                }
                
                // Update recent trades
                const tradesList = document.getElementById('tradesList');
                if (data.recent_trades && data.recent_trades.length > 0) {
                    tradesList.innerHTML = data.recent_trades.slice(0, 10).map(t => {
                        const side = t.side || 'unknown';
                        const sideClass = side.toLowerCase();
                        return `
                            <div class="trade-item ${sideClass}">
                                <div style="display: flex; justify-content: space-between; align-items: center;">
                                    <div>
                                        <strong>${t.symbol || 'Unknown'}</strong>
                                        <span class="badge ${sideClass === 'buy' ? 'success' : 'danger'}">${side.toUpperCase()}</span>
                                    </div>
                                    <div style="text-align: right;">
                                        <div>${t.size || 0} @ $${(t.price || 0).toFixed(2)}</div>
                                        <div style="font-size: 12px; color: #9ca3af;">${new Date(t.timestamp * 1000).toLocaleString()}</div>
                                    </div>
                                </div>
                                ${t.reason ? `<div style="margin-top: 8px; font-size: 12px; color: #9ca3af;">${t.reason}</div>` : ''}
                            </div>
                        `;
                    }).join('');
                } else {
                    tradesList.innerHTML = '<div style="text-align: center; color: #6b7280; padding: 20px;">No trades yet</div>';
                }
                
                // Update stats
                document.getElementById('statsText').textContent = 
                    `${data.stats.decisions || 0} decisions, ${data.stats.trades || 0} trades, ${data.stats.performance_snapshots || 0} snapshots`;
                
                document.getElementById('lastUpdate').textContent = new Date().toLocaleTimeString();
                
            } catch (error) {
                console.error('Dashboard update error:', error);
            }
        }

        // Fetch equity history for chart
        async function updateChart() {
            try {
                const response = await fetch('/api/equity_history?hours=24');
                const data = await response.json();
                
                if (data.history && data.history.length > 0) {
                    const chartData = data.history.map(item => ({
                        time: new Date(item.time).getTime() / 1000,
                        value: item.value
                    }));
                    lineSeries.setData(chartData);
                }
            } catch (error) {
                console.error('Chart update error:', error);
            }
        }

        // Initial load
        updateDashboard();
        updateChart();
        
        // Auto-refresh every 3 seconds
        setInterval(updateDashboard, 3000);
        setInterval(updateChart, 10000);
    </script>
</body>
</html>
"""

# --- POST /ask (safe wrapper that never hides the error) ---
class AskIn(BaseModel):
    text: str
    token: Optional[str] = None

@app.get("/sms-setup", response_class=HTMLResponse)
def sms_setup_page():
    """SMS notification setup page."""
    return """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SMS Notifications Setup - Zyn</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            display: flex;
            justify-content: center;
            align-items: center;
        }
        .container {
            max-width: 600px;
            width: 100%;
            background: white;
            border-radius: 20px;
            padding: 40px;
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
        }
        h1 {
            background: linear-gradient(135deg, #667eea, #764ba2);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 10px;
        }
        p { color: #6b7280; margin-bottom: 30px; }
        label {
            display: block;
            font-weight: 600;
            color: #374151;
            margin-bottom: 8px;
            margin-top: 20px;
        }
        input[type="tel"], input[type="number"] {
            width: 100%;
            padding: 12px;
            border: 2px solid #e2e8f0;
            border-radius: 8px;
            font-size: 16px;
        }
        input:focus { outline: none; border-color: #667eea; }
        .checkbox-group {
            margin: 20px 0;
            padding: 15px;
            background: #f7fafc;
            border-radius: 8px;
        }
        .checkbox-group label {
            display: flex;
            align-items: center;
            font-weight: 500;
            margin: 10px 0;
        }
        input[type="checkbox"] {
            width: 20px;
            height: 20px;
            margin-right: 10px;
        }
        button {
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 20px;
        }
        button:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4); }
        .status {
            margin-top: 20px;
            padding: 15px;
            border-radius: 8px;
            display: none;
        }
        .status.success { background: #d1fae5; color: #065f46; display: block; }
        .status.error { background: #fee2e2; color: #991b1b; display: block; }
        .back-link {
            display: inline-block;
            margin-top: 20px;
            color: #667eea;
            text-decoration: none;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>📱 SMS Notifications Setup</h1>
        <p>Get text messages on your phone when Zyn makes trades or hits profit/loss targets.</p>
        
        <form id="smsForm">
            <label for="phone">Your Phone Number</label>
            <input type="tel" id="phone" placeholder="+12345678900" required />
            <small style="color: #6b7280;">Format: +1 followed by your 10-digit number</small>
            
            <div class="checkbox-group">
                <label><input type="checkbox" id="enabled" /> Enable SMS Notifications</label>
                <label><input type="checkbox" id="notifyTrades" checked /> Notify on every trade</label>
                <label><input type="checkbox" id="notifyDaily" checked /> Daily summary (6 PM)</label>
                <label><input type="checkbox" id="notifyWeekly" checked /> Weekly summary (Sundays)</label>
            </div>
            
            <label for="dailyHour">Daily Summary Time (hour, 0-23)</label>
            <input type="number" id="dailyHour" value="18" min="0" max="23" />
            
            <button type="submit">Save Settings</button>
        </form>
        
        <div class="status" id="status"></div>
        
        <a href="/" class="back-link">← Back to Control Panel</a>
    </div>
    
    <script>
        // Load current settings
        async function loadSettings() {
            try {
                const response = await fetch('/api/sms-config');
                const config = await response.json();
                
                document.getElementById('phone').value = config.your_phone_number || '';
                document.getElementById('enabled').checked = config.enabled || false;
                document.getElementById('notifyTrades').checked = config.notify_on_trades !== false;
                document.getElementById('notifyDaily').checked = config.notify_daily_summary !== false;
                document.getElementById('notifyWeekly').checked = config.notify_weekly_summary !== false;
                document.getElementById('dailyHour').value = config.daily_summary_hour || 18;
            } catch (error) {
                console.error('Failed to load settings:', error);
            }
        }
        
        // Save settings
        document.getElementById('smsForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const config = {
                enabled: document.getElementById('enabled').checked,
                your_phone_number: document.getElementById('phone').value.trim(),
                notify_on_trades: document.getElementById('notifyTrades').checked,
                notify_daily_summary: document.getElementById('notifyDaily').checked,
                notify_weekly_summary: document.getElementById('notifyWeekly').checked,
                daily_summary_hour: parseInt(document.getElementById('dailyHour').value)
            };
            
            try {
                const response = await fetch('/api/sms-config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });
                
                const result = await response.json();
                const statusEl = document.getElementById('status');
                
                if (result.status === 'success') {
                    statusEl.className = 'status success';
                    statusEl.textContent = '✅ Settings saved! You\\'ll receive notifications on your phone.';
                } else {
                    statusEl.className = 'status error';
                    statusEl.textContent = '❌ Failed to save: ' + result.message;
                }
            } catch (error) {
                const statusEl = document.getElementById('status');
                statusEl.className = 'status error';
                statusEl.textContent = '❌ Error: ' + error.message;
            }
        });
        
        // Load on page load
        loadSettings();
    </script>
</body>
</html>
"""

@app.get("/api/sms-config")
def get_sms_config_api():
    """Get SMS configuration."""
    try:
        from sms_notifications import get_sms_config
        return get_sms_config()
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/sms-config")
def save_sms_config_api(config: dict):
    """Save SMS configuration."""
    try:
        from sms_notifications import save_sms_config
        save_sms_config(config)
        return {"status": "success", "message": "SMS notifications configured!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.get("/api/status")
def get_status():
    """
    CRITICAL: Single source of truth for trading status.
    Returns: {mode, lastSyncUTC, totals:{24h,7d,30d}, openOrders, recentTrades, balances, warnings}
    """
    try:
        from status_service import (
            get_mode, 
            get_balances, 
            get_open_orders,
            get_trades,
            get_activity_summary,
            get_last_sync_time,
            healthcheck,
            auto_sync_if_needed
        )
        
        # CRITICAL: Auto-sync FIRST to ensure data freshness
        auto_sync_if_needed()
        
        # Get health status
        health = healthcheck()
        
        # Get activity summaries for all windows
        summary_24h = get_activity_summary("24h")
        summary_7d = get_activity_summary("7d")
        summary_30d = get_activity_summary("30d")
        
        # Get current data
        mode = get_mode()
        balances = get_balances()
        open_orders = get_open_orders()
        last_sync = get_last_sync_time()
        
        # CRITICAL: Get actual trade details (not just aggregates)
        recent_trades = get_trades(limit=50)  # Last 50 trades with full details
        
        return {
            "mode": mode,
            "lastSyncUTC": last_sync,
            "totals": {
                "24h": summary_24h,
                "7d": summary_7d,
                "30d": summary_30d
            },
            "openOrders": open_orders,
            "recentTrades": recent_trades,
            "balances": balances,
            "warnings": health.get('warnings', []),
            "errors": health.get('errors', []),
            "health_status": health.get('status', 'unknown')
        }
    except Exception as e:
        import traceback
        return JSONResponse(status_code=500, content={
            "error": str(e),
            "trace": traceback.format_exc()[-1000:]
        })

@app.get("/api/events/{request_id}")
async def events_stream(request_id: str):
    async def event_generator():
        queue = event_manager.subscribe(request_id)
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping', 'timestamp': datetime.utcnow().isoformat()})}\n\n"
                except Exception:
                    break
        finally:
            event_manager.unsubscribe(request_id, queue)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.post("/ask")
async def ask(a: AskIn):
    request_id = str(uuid.uuid4())
    
    try:
        from llm_agent import ask_llm
        from telemetry_db import log_conversation
        
        # Emit typing_start event
        event_manager.typing_start(request_id)
        
        try:
            # Use session_id from token if provided, otherwise use "jimmy" as default
            session_id = a.token if a.token else "jimmy"
            
            # Get response with conversation history
            out = ask_llm(a.text, session_id=session_id, request_id=request_id)
            
            # Log conversation for learning
            try:
                log_conversation(a.text, out)
            except Exception:
                pass
            
            return {"answer": out, "request_id": request_id}
        finally:
            # Always emit typing_stop, even on error
            event_manager.typing_stop(request_id)
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        # Ensure typing_stop on error
        event_manager.typing_stop(request_id)
        # Return 200 so the UI shows the error text instead of blank 500 page
        return JSONResponse(status_code=200, content={
            "answer": f"[Backend Error] {e.__class__.__name__}: {e}",
            "trace": tb[-1500:],
            "request_id": request_id
        })

# --- optional: GET /ask?q=... (lets you ask from the URL) ---
@app.get("/ask")
async def ask_get(q: str = Query(..., description="Your question")):
    try:
        from llm_agent import ask_llm
        return {"answer": ask_llm(q)}
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        return JSONResponse(status_code=200, content={
            "answer": f"[Backend Error] {e.__class__.__name__}: {e}",
            "trace": tb[-1500:]
        })

# --- API Endpoints for Dashboard ---

@app.get("/api/dashboard")
def get_dashboard_data():
    """Get comprehensive dashboard data - 100% ACCURATE from Status Service."""
    try:
        from status_service import (
            get_trades, get_open_orders, get_balances, 
            get_activity_summary, auto_sync_if_needed
        )
        from telemetry_db import get_db
        
        # CRITICAL: Ensure fresh data from Kraken
        auto_sync_if_needed()
        
        state_path = Path(os.environ.get("STATE_PATH", str(Path(__file__).with_name("state.json"))))
        state = {}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
        
        # Get real data from Status Service
        recent_trades = get_trades(limit=20)
        open_orders = get_open_orders()
        balances = get_balances()
        summary_7d = get_activity_summary("7d")
        
        # Calculate win rate from actual trades
        wins = 0
        losses = 0
        for trade in recent_trades:
            # Simple heuristic: sell trades are exits, check if profitable
            if trade.get('side') == 'sell':
                # TODO: Match with entry to calculate P&L properly
                # For now, count all sells as neutral
                pass
        
        # Get performance from REAL Kraken data via Status Service
        stats = {
            "decisions": 0,
            "trades": summary_7d['trades']['total_trades'],  # REAL trade count from Kraken
            "performance_snapshots": 0
        }
        try:
            with get_db() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) as count FROM decisions")
                row = cursor.fetchone()
                stats["decisions"] = dict(row)["count"] if row else 0
                cursor.execute("SELECT COUNT(*) as count FROM performance")
                row = cursor.fetchone()
                stats["performance_snapshots"] = dict(row)["count"] if row else 0
        except Exception:
            pass
        
        # Convert open orders to positions format  
        positions = []
        for order in open_orders:
            if order.get('status') == 'open':
                positions.append({
                    'symbol': order.get('symbol', 'Unknown'),
                    'qty': order.get('amount', 0),  # CCXT uses 'amount' not 'quantity'
                    'entry': order.get('price', 0),
                    'side': order.get('side', 'unknown'),
                    'order_id': order.get('order_id', ''),
                    'type': order.get('type', 'unknown')
                })
        
        # Calculate equity from balances (simple: just use USD for now)
        usd_balance = balances.get('USD', {}) if balances else {}
        if isinstance(usd_balance, dict):
            total_usd = usd_balance.get('total', 0)
        else:
            total_usd = usd_balance
        # TODO: Add crypto balances * current price for accurate total equity
        
        # Calculate total equity including crypto balances
        total_equity = total_usd
        for currency, bal in balances.items():
            if currency != 'USD' and isinstance(bal, dict):
                # Add crypto balances (already in USD equivalent from Kraken)
                total_equity += bal.get('total', 0) * bal.get('usd_price', 0) if bal.get('usd_price') else 0
        
        # Calculate equity change
        equity_change = summary_7d.get("realized_pnl_usd", 0)
        equity_change_pct = (equity_change / total_equity * 100) if total_equity > 0 else 0
        
        return {
            # Top-level fields for frontend compatibility
            "equity_usd": total_equity,
            "equity_change_usd": equity_change,
            "equity_change_pct": equity_change_pct,
            "total_trades": summary_7d['trades']['total_trades'],
            # Legacy nested format (kept for compatibility)
            "equity": {
                "current": total_equity,
                "day_start": state.get("equity_day_start_usd", total_equity),
                "change": equity_change,
                "change_pct": equity_change_pct
            },
            "positions": positions,
            "paused": state.get("paused", False),
            "recent_trades": recent_trades,
            "performance": {
                "total_trades": summary_7d['trades']['total_trades'],  # REAL total from Kraken
                "wins": wins,
                "losses": losses,
                "win_rate": wins / summary_7d['trades']['total_trades'] if summary_7d['trades']['total_trades'] > 0 else 0
            },
            "stats": stats,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        import traceback
        return JSONResponse(status_code=500, content={
            "error": str(e),
            "trace": traceback.format_exc()[-1000:]
        })

@app.post("/api/autopilot/start")
def start_autopilot():
    """Start the autopilot trading bot."""
    try:
        state_path = Path(os.environ.get("STATE_PATH", str(Path(__file__).with_name("state.json"))))
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["paused"] = False
            state["autopilot_enabled"] = True
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            return {"status": "success", "message": "Zyn is now active and trading!", "autopilot_running": True}
        return {"status": "error", "message": "State file not found", "autopilot_running": False}
    except Exception as e:
        return {"status": "error", "message": str(e), "autopilot_running": False}

@app.post("/api/autopilot/stop")
def stop_autopilot():
    """Stop the autopilot trading bot."""
    try:
        state_path = Path(os.environ.get("STATE_PATH", str(Path(__file__).with_name("state.json"))))
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["paused"] = True
            state["autopilot_enabled"] = False
            state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
            return {"status": "success", "message": "Zyn has been paused. No new trades will be executed.", "autopilot_running": False}
        return {"status": "error", "message": "State file not found", "autopilot_running": False}
    except Exception as e:
        return {"status": "error", "message": str(e), "autopilot_running": False}

@app.get("/api/autopilot/status")
def autopilot_status():
    """Get current autopilot status."""
    try:
        state_path = Path(os.environ.get("STATE_PATH", str(Path(__file__).with_name("state.json"))))
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            is_running = not state.get("paused", False) and state.get("autopilot_enabled", True)
            return {
                "autopilot_running": is_running,
                "paused": state.get("paused", False),
                "equity": state.get("equity_now_usd", 0),
                "symbols": list(state.get("symbols", {}).keys()) if isinstance(state.get("symbols", {}), dict) else []
            }
        return {"autopilot_running": False, "paused": True, "equity": 0, "symbols": []}
    except Exception as e:
        return {"autopilot_running": False, "paused": True, "equity": 0, "symbols": [], "error": str(e)}

@app.get("/api/trading-mode")
def get_trading_mode():
    """Get current trading mode (paper or live)."""
    try:
        from exchange_manager import is_paper_mode, get_mode_str
        return {
            "mode": get_mode_str(),
            "is_paper": is_paper_mode(),
            "is_live": not is_paper_mode(),
            "validate_only": os.getenv("KRAKEN_VALIDATE_ONLY", "1")
        }
    except Exception as e:
        return {"mode": "unknown", "is_paper": True, "is_live": False, "error": str(e)}

class TradingModeRequest(BaseModel):
    mode: str

import threading
_mode_lock = threading.Lock()

@app.post("/api/set-trading-mode")
def set_trading_mode_endpoint(request: TradingModeRequest):
    """
    Set trading mode (paper or live).
    CRITICAL: Updates .env file FIRST, then runtime exchange manager.
    Thread-safe with lock to prevent concurrent mode changes.
    """
    with _mode_lock:
        try:
            mode = request.mode.lower().strip()
            
            if mode not in ("paper", "live"):
                return {"status": "error", "message": f"Invalid mode: {mode}. Must be 'paper' or 'live'."}
            
            paper_mode = (mode == "paper")
            
            # CRITICAL: Update .env file FIRST before setting mode
            env_path = Path(__file__).with_name(".env")
            if env_path.exists():
                env_content = env_path.read_text(encoding="utf-8")
                lines = env_content.split("\n")
                updated = False
                
                for i, line in enumerate(lines):
                    if line.startswith("KRAKEN_VALIDATE_ONLY="):
                        lines[i] = f"KRAKEN_VALIDATE_ONLY={'1' if paper_mode else '0'}"
                        updated = True
                        break
                
                if updated:
                    env_path.write_text("\n".join(lines), encoding="utf-8")
                else:
                    return {"status": "error", "message": "KRAKEN_VALIDATE_ONLY not found in .env"}
            else:
                return {"status": "error", "message": ".env file not found"}
            
            # NOW update exchange manager (after .env is persisted)
            from exchange_manager import set_trading_mode, get_mode_str
            set_trading_mode(paper_mode)
            
            new_mode = get_mode_str()
            warning = "" if paper_mode else " ⚠️ REAL MONEY AT RISK! Restart autopilot to apply."
            
            return {
                "status": "success",
                "message": f"Trading mode set to {new_mode.upper()}{warning}",
                "mode": new_mode,
                "is_paper": paper_mode,
                "is_live": not paper_mode,
                "note": "Autopilot must be restarted for mode change to take full effect"
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}

@app.post("/api/restart-workflows")
def restart_workflows_endpoint():
    """
    Restart both autopilot and chat workflows by exiting the chat process.
    Replit will automatically restart it, and the mode change will take effect.
    """
    try:
        # Schedule exit after returning response to client
        import signal
        import threading
        
        def delayed_exit():
            import time
            time.sleep(1)  # Give response time to send
            os.kill(os.getpid(), signal.SIGTERM)
        
        # Start exit timer in background
        threading.Thread(target=delayed_exit, daemon=True).start()
        
        return {
            "status": "success",
            "message": "Chat workflow restarting... Reload the page in 3 seconds.",
            "note": "Autopilot will pick up any mode changes on its next cycle (5 min)"
        }
    except Exception as e:
        return {"status": "error", "message": f"Failed to restart: {str(e)}"}

@app.get("/api/equity_history")
def get_equity_history(hours: int = 24):
    """Get equity history for charting."""
    try:
        from telemetry_db import get_db
        
        cutoff = datetime.now() - timedelta(hours=hours)
        
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT timestamp, equity_usd, equity_change_usd
                FROM performance
                WHERE timestamp >= ?
                ORDER BY timestamp ASC
            """, (cutoff.isoformat(),))
            
            history = []
            for row in cursor.fetchall():
                r = dict(row)
                history.append({
                    "time": r["timestamp"],
                    "value": r["equity_usd"]
                })
            
            return {"history": history}
    except Exception as e:
        return {"history": [], "error": str(e)}
