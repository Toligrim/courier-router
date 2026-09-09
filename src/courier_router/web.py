from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import html
import json
import os
import secrets
import shutil
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from .cli import cmd_plan

RUNS_ROOT = Path(os.getenv("WEB_RUNS_PATH", "data/web/runs"))
USERS_PATH = Path(os.getenv("WEB_USERS_PATH", "data/web/users.json"))
MAX_UPLOAD_BYTES = int(os.getenv("WEB_MAX_UPLOAD_MB", "10")) * 1024 * 1024

CSS = """
:root {
  --color-scheme: light;
  --transparent: transparent;
  --bg: #f3f5f8;
  --surface: #ffffff;
  --surface-2: #f7f8fa;
  --surface-3: #edf0f4;
  --surface-elevated: #ffffff;
  --ink: #15191f;
  --muted: #68707d;
  --line: #dde1e7;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --accent-ink: #ffffff;
  --ok: #18794a;
  --ok-soft: #e6f5ed;
  --warn: #a96000;
  --warn-soft: #fff2cf;
  --danger: #c53131;
  --danger-soft: #fdeaea;
  --pickup: #218653;
  --pickup-soft: #e6f5ed;
  --delivery: #bf670a;
  --delivery-soft: #fff0df;
  --marker-ink: #ffffff;
  --focus: #7aa7ff;
  --overlay: rgba(10, 14, 20, 0.58);
  --shadow: 0 12px 34px rgba(15, 23, 42, 0.08);
  --shadow-float: 0 18px 48px rgba(15, 23, 42, 0.18);
  --map-filter: none;
}

@media (prefers-color-scheme: dark) {
  :root {
    --color-scheme: dark;
    --bg: #0d1015;
    --surface: #151a21;
    --surface-2: #1b212a;
    --surface-3: #232a35;
    --surface-elevated: #1a2029;
    --ink: #f2f4f7;
    --muted: #a7afbb;
    --line: #303846;
    --accent: #72a6ff;
    --accent-hover: #8bb5ff;
    --accent-ink: #07111f;
    --ok: #58cb8d;
    --ok-soft: #173425;
    --warn: #efb44f;
    --warn-soft: #3a2d14;
    --danger: #ff7b7b;
    --danger-soft: #3d1f23;
    --pickup: #4ebb7d;
    --pickup-soft: #183728;
    --delivery: #f2a24b;
    --delivery-soft: #3b2a17;
    --marker-ink: #08120d;
    --focus: #9bbcff;
    --overlay: rgba(0, 0, 0, 0.72);
    --shadow: 0 12px 34px rgba(0, 0, 0, 0.24);
    --shadow-float: 0 18px 48px rgba(0, 0, 0, 0.42);
    --map-filter: brightness(0.72) contrast(1.08) saturate(0.78);
  }
}

* {
  box-sizing: border-box;
}

html {
  background: var(--bg);
  color-scheme: var(--color-scheme);
  -webkit-text-size-adjust: 100%;
}

body {
  margin: 0;
  min-height: 100vh;
  min-height: 100dvh;
  overflow-x: hidden;
  padding: 0 env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  font-size: 15px;
  line-height: 1.45;
}

button,
input,
select {
  font: inherit;
}

input,
select,
textarea {
  max-width: 100%;
}

button,
input,
select,
a,
summary {
  -webkit-tap-highlight-color: transparent;
}

button,
summary,
a {
  touch-action: manipulation;
}

a {
  color: var(--accent);
}

h1,
h2,
h3,
p {
  margin-top: 0;
}

h1,
h2,
h3 {
  color: var(--ink);
  text-wrap: balance;
}

h1 {
  margin-bottom: 8px;
  font-size: 22px;
  line-height: 1.2;
  font-weight: 780;
  letter-spacing: -0.02em;
}

h2 {
  margin-bottom: 12px;
  font-size: 17px;
  line-height: 1.3;
  font-weight: 760;
}

h3 {
  font-size: 17px;
  line-height: 1.35;
  font-weight: 740;
}

.top {
  position: sticky;
  top: 0;
  z-index: 1000;
  display: flex;
  min-height: calc(48px + env(safe-area-inset-top));
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: env(safe-area-inset-top) 16px 0;
  border-bottom: 1px solid var(--line);
  background: var(--surface);
}

.brand,
.top-link {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  text-decoration: none;
}

.brand {
  color: var(--ink);
  font-size: 15px;
  font-weight: 800;
  letter-spacing: -0.01em;
}

.account {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.account-name {
  max-width: 124px;
  overflow: hidden;
  color: var(--muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.top-link {
  color: var(--ink);
  font-weight: 650;
}

.wrap {
  width: 100%;
  max-width: 1120px;
  margin: 0 auto;
  padding: 16px 16px 24px;
}

.card {
  min-width: 0;
  margin-bottom: 16px;
  padding: 20px;
  border: 1px solid var(--line);
  border-radius: 20px;
  background: var(--surface);
  box-shadow: var(--shadow);
}

.muted {
  color: var(--muted);
}

.lead {
  margin-bottom: 18px;
  color: var(--muted);
}

.field {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 7px;
  margin-bottom: 14px;
}

.field label,
.field-label {
  color: var(--ink);
  font-size: 13px;
  font-weight: 680;
}

.field input,
.field select {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 48px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 12px;
  outline: 0;
  background: var(--surface);
  color: var(--ink);
  font-size: 16px;
}

.field select {
  -webkit-appearance: none;
  appearance: none;
  overflow: hidden;
  padding-right: 34px;
  text-overflow: ellipsis;
  white-space: nowrap;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath fill='%2368707d' d='M1 1l5 5 5-5'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
}

.field input:focus,
.field select:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--focus);
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.btn {
  display: inline-flex;
  min-height: 44px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 10px 16px;
  border: 1px solid var(--transparent);
  border-radius: 12px;
  background: var(--accent);
  color: var(--accent-ink);
  font-size: 15px;
  font-weight: 720;
  line-height: 1.2;
  text-align: center;
  text-decoration: none;
  cursor: pointer;
  transition: background 140ms ease, transform 140ms ease, border-color 140ms ease;
}

.btn:hover {
  background: var(--accent-hover);
}

.btn:active {
  transform: translateY(1px);
}

.btn:focus-visible,
.icon-btn:focus-visible,
.segment-btn:focus-visible,
.menu-danger:focus-visible,
.stop:focus-visible,
.upload:focus-within {
  outline: 3px solid var(--focus);
  outline-offset: 2px;
}

.btn.secondary {
  border-color: var(--line);
  background: var(--surface-3);
  color: var(--ink);
}

.btn.secondary:hover {
  background: var(--surface-2);
}

.btn[disabled] {
  opacity: 0.5;
  cursor: not-allowed;
  transform: none;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 14px;
}

.check-row {
  display: flex;
  min-height: 44px;
  align-items: center;
  gap: 10px;
  color: var(--ink);
  cursor: pointer;
}

.check-row input {
  width: 20px;
  height: 20px;
  margin: 0;
  accent-color: var(--accent);
}

.upload {
  position: relative;
  display: grid;
  min-height: 170px;
  place-items: center;
  margin-bottom: 18px;
  padding: 24px;
  border: 2px dashed var(--line);
  border-radius: 18px;
  background: var(--surface-2);
  text-align: center;
  cursor: pointer;
  transition: border-color 140ms ease, background 140ms ease, transform 140ms ease;
}

.upload:hover,
.upload.is-dragging,
.upload.has-file {
  border-color: var(--accent);
  background: var(--surface-3);
}

.upload.is-dragging {
  transform: scale(0.995);
}

.file-input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  opacity: 0;
  pointer-events: none;
}

.upload-content {
  display: grid;
  min-width: 0;
  place-items: center;
  gap: 6px;
}

.upload-icon {
  display: inline-flex;
  width: 46px;
  height: 46px;
  align-items: center;
  justify-content: center;
  border-radius: 14px;
  background: var(--surface);
  color: var(--accent);
  font-size: 24px;
  font-weight: 800;
  box-shadow: var(--shadow);
}

.upload-title {
  margin-top: 4px;
  color: var(--ink);
  font-size: 17px;
  font-weight: 740;
}

.upload-hint,
.upload-file-name {
  max-width: 100%;
  color: var(--muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.upload-file-name {
  color: var(--ink);
  font-weight: 680;
}

.error {
  margin-bottom: 14px;
  padding: 13px 14px;
  border: 1px solid var(--danger);
  border-radius: 14px;
  background: var(--danger-soft);
  color: var(--ink);
}

.error-summary {
  font-weight: 720;
  overflow-wrap: anywhere;
}

.error details {
  margin-top: 8px;
}

.error details summary {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  color: var(--danger);
  font-size: 13px;
  font-weight: 700;
  cursor: pointer;
}

.error pre {
  max-height: 280px;
  margin: 0;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: var(--muted);
  font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.build-overlay[hidden] {
  display: none;
}

.build-overlay {
  position: fixed;
  inset: 0;
  z-index: 5000;
  display: grid;
  place-items: center;
  padding: calc(20px + env(safe-area-inset-top)) calc(20px + env(safe-area-inset-right)) calc(20px + env(safe-area-inset-bottom)) calc(20px + env(safe-area-inset-left));
  background: var(--overlay);
}

.build-overlay-card {
  width: min(100%, 360px);
  padding: 26px;
  border: 1px solid var(--line);
  border-radius: 22px;
  background: var(--surface-elevated);
  color: var(--ink);
  text-align: center;
  box-shadow: var(--shadow-float);
}

.spinner {
  width: 38px;
  height: 38px;
  margin: 0 auto 16px;
  border: 3px solid var(--line);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.85s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

.run-list {
  display: grid;
}

.run {
  display: flex;
  min-width: 0;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 0;
  border-top: 1px solid var(--line);
}

.run:first-child {
  border-top: 0;
}

.run-main {
  min-width: 0;
  flex: 1;
}

.run-title-row {
  display: flex;
  min-width: 0;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  margin-bottom: 3px;
}

.run-title {
  color: var(--ink);
  font-size: 17px;
  font-weight: 740;
}

.run-meta {
  color: var(--muted);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.run-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

.status-chip,
.pill {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 720;
  line-height: 1.2;
}

.status-chip.ok {
  background: var(--ok-soft);
  color: var(--ok);
}

.status-chip.review,
.pill.review {
  background: var(--warn-soft);
  color: var(--warn);
}

.status-chip.neutral,
.pill.neutral {
  background: var(--surface-3);
  color: var(--ink);
}

.pill.pickup {
  background: var(--pickup-soft);
  color: var(--pickup);
}

.pill.delivery {
  background: var(--delivery-soft);
  color: var(--delivery);
}

.more-menu {
  position: relative;
  flex: 0 0 auto;
}

.more-menu > summary {
  list-style: none;
}

.more-menu > summary::-webkit-details-marker {
  display: none;
}

.icon-btn {
  display: inline-flex;
  width: 44px;
  min-height: 44px;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--surface-2);
  color: var(--ink);
  font-size: 20px;
  font-weight: 800;
  line-height: 1;
  cursor: pointer;
}

.menu-popover {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  z-index: 50;
  min-width: 210px;
  padding: 6px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-elevated);
  box-shadow: var(--shadow-float);
}

.menu-popover form {
  margin: 0;
}

.menu-danger {
  width: 100%;
  min-height: 44px;
  padding: 8px 10px;
  border: 0;
  border-radius: 10px;
  background: var(--transparent);
  color: var(--danger);
  font-weight: 700;
  text-align: left;
  cursor: pointer;
}

.menu-danger:hover {
  background: var(--danger-soft);
}

.login {
  width: min(100%, 420px);
  margin: max(6vh, calc(24px + env(safe-area-inset-top))) auto;
  padding: 28px;
}

.login .btn {
  width: 100%;
}

.login-badge {
  display: inline-flex;
  min-height: 28px;
  align-items: center;
  margin-bottom: 14px;
  padding: 4px 9px;
  border-radius: 999px;
  background: var(--surface-3);
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
}

.route-page {
  min-width: 0;
}

.route-page.has-bottom-action {
  padding-bottom: 82px;
}

.summary-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.route-meta {
  margin-bottom: 14px;
  color: var(--muted);
  font-size: 13px;
}

.metric-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.metric {
  min-width: 0;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 16px;
  background: var(--surface-2);
}

.metric-label {
  display: block;
  margin-bottom: 3px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 620;
}

.metric-value {
  display: block;
  color: var(--ink);
  font-size: 22px;
  font-weight: 800;
  letter-spacing: -0.02em;
  overflow-wrap: anywhere;
}

.route-status-line {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 7px;
  margin-top: 12px;
  color: var(--muted);
  font-size: 13px;
}

.route-summary-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-top: 14px;
}

.navigator-note {
  margin: 10px 0 0;
  color: var(--muted);
  font-size: 12px;
}

.view-switch {
  display: none;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px;
  margin-bottom: 12px;
  padding: 4px;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface-3);
}

.segment-btn {
  min-height: 44px;
  border: 0;
  border-radius: 10px;
  background: var(--transparent);
  color: var(--muted);
  font-weight: 720;
  cursor: pointer;
}

.segment-btn.is-active {
  background: var(--surface);
  color: var(--ink);
  box-shadow: var(--shadow);
}

.route-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(360px, 0.8fr);
  align-items: start;
  gap: 16px;
}

.map-shell {
  min-width: 0;
  padding: 0;
  overflow: hidden;
}

.map {
  width: 100%;
  height: 72vh;
  min-height: 520px;
  background: var(--surface-2);
}

.stops {
  min-width: 0;
  max-height: 72vh;
  overflow: auto;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}

.stop {
  min-width: 0;
  margin-bottom: 10px;
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 17px;
  outline: 0;
  background: var(--surface);
  box-shadow: var(--shadow);
  cursor: pointer;
  scroll-margin-top: 110px;
}

.stop:hover {
  border-color: var(--accent);
}

.stop-heading-row {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 10px;
}

.seq {
  display: inline-flex;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  color: var(--marker-ink);
  font-size: 13px;
  font-weight: 820;
}

.seq.pickup {
  background: var(--pickup);
}

.seq.delivery {
  background: var(--delivery);
}

.stop-address {
  min-width: 0;
  margin: 1px 0 0;
  color: var(--ink);
  font-size: 17px;
  font-weight: 760;
  line-height: 1.32;
  overflow-wrap: anywhere;
}

.raw-address {
  margin: 6px 0 0 44px;
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}

.pill-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin: 10px 0;
}

.coord-note,
.stop-warning {
  margin: 8px 0;
  padding: 9px 10px;
  border: 1px solid var(--warn);
  border-radius: 11px;
  background: var(--warn-soft);
  color: var(--ink);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.stop-meta {
  display: grid;
  gap: 4px;
  margin: 9px 0;
  color: var(--ink);
  font-size: 13px;
}

.stop-meta p,
.stop-comment {
  margin: 0;
}

.stop-comment {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--line);
  color: var(--ink);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.phone-link {
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  color: var(--accent);
  font-weight: 700;
  text-decoration: none;
}

.stop-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}

.stop-actions .btn {
  flex: 1 1 170px;
}

.leaflet-div-icon {
  border: 0 !important;
  background: var(--transparent) !important;
}

.marker-num {
  --marker-bg: var(--delivery);
  display: flex;
  width: 36px;
  height: 36px;
  align-items: center;
  justify-content: center;
  border: 3px solid var(--surface-elevated);
  border-radius: 50%;
  outline: 0 solid var(--transparent);
  background: var(--marker-bg);
  color: var(--marker-ink);
  box-shadow: var(--shadow-float);
  font-size: 13px;
  font-weight: 840;
}

.marker-num.pickup {
  --marker-bg: var(--pickup);
}

.marker-num.delivery {
  --marker-bg: var(--delivery);
}

.marker-num.review {
  outline: 4px solid var(--warn);
  outline-offset: 1px;
}

.leaflet-tile-pane {
  filter: var(--map-filter);
}

.leaflet-control-zoom a,
.leaflet-control-attribution,
.leaflet-popup-content-wrapper,
.leaflet-popup-tip {
  border-color: var(--line) !important;
  background: var(--surface-elevated) !important;
  color: var(--ink) !important;
}

.leaflet-control-zoom a {
  color: var(--ink) !important;
}

.leaflet-control-attribution a,
.leaflet-popup-content a {
  color: var(--accent) !important;
}

.leaflet-popup-content-wrapper {
  border: 1px solid var(--line);
  border-radius: 13px;
  box-shadow: var(--shadow-float);
}

.bottom-action {
  position: fixed;
  right: 0;
  bottom: 0;
  left: 0;
  z-index: 1200;
  padding: 10px calc(16px + env(safe-area-inset-right)) calc(10px + env(safe-area-inset-bottom)) calc(16px + env(safe-area-inset-left));
  border-top: 1px solid var(--line);
  background: var(--surface);
  box-shadow: var(--shadow-float);
}

.bottom-action-inner {
  display: flex;
  max-width: 1120px;
  margin: 0 auto;
  justify-content: flex-end;
}

.bottom-action .btn {
  min-width: 320px;
}

.empty-state {
  padding: 20px 0 4px;
  color: var(--muted);
  text-align: center;
}

@media (max-width: 800px) {
  .wrap {
    padding: 12px 12px 20px;
  }

  .card {
    padding: 14px;
    border-radius: 17px;
  }

  .form-grid {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .upload {
    min-height: 154px;
    padding: 20px 14px;
  }

  .run {
    align-items: flex-start;
  }

  .run-actions .btn {
    padding-right: 12px;
    padding-left: 12px;
  }

  .summary-head {
    align-items: center;
  }

  .view-switch {
    position: sticky;
    top: calc(52px + env(safe-area-inset-top));
    z-index: 850;
    display: grid;
  }

  .route-layout {
    display: block;
  }

  .route-page[data-route-view="list"] [data-route-pane="map"] {
    display: none;
  }

  .route-page[data-route-view="map"] [data-route-pane="list"] {
    display: none;
  }

  .map {
    height: calc(100dvh - 190px);
    min-height: 390px;
    max-height: 660px;
  }

  .stops {
    max-height: none;
    overflow: visible;
    scrollbar-gutter: auto;
  }

  .stop {
    padding: 13px;
  }

  .raw-address {
    margin-left: 44px;
  }

  .bottom-action .btn {
    width: 100%;
    min-width: 0;
  }

  .metric-value {
    font-size: 20px;
  }
}

@media (max-width: 420px) {
  .account-name {
    display: none;
  }

  .metric {
    padding: 12px;
  }

  .run-actions .btn {
    font-size: 13px;
  }

  .stop-actions .btn {
    flex-basis: 100%;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    animation: none !important;
    transition: none !important;
  }
}
"""

HOME_JS = """<script>
(() => {
  const form = document.getElementById('route-form');
  const input = document.getElementById('route-file');
  const zone = document.getElementById('drop-zone');
  const fileName = document.getElementById('selected-file');
  const submit = document.getElementById('build-submit');
  const overlay = document.getElementById('build-overlay');
  if (!form || !input || !zone || !fileName || !submit || !overlay) return;

  const syncFileState = () => {
    const file = input.files && input.files[0];
    submit.disabled = !file;
    zone.classList.toggle('has-file', Boolean(file));
    fileName.textContent = file ? file.name : 'Файл пока не выбран';
  };

  input.addEventListener('change', syncFileState);

  ['dragenter', 'dragover'].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.add('is-dragging');
    });
  });

  ['dragleave', 'drop'].forEach((eventName) => {
    zone.addEventListener(eventName, (event) => {
      event.preventDefault();
      zone.classList.remove('is-dragging');
    });
  });

  zone.addEventListener('drop', (event) => {
    const files = event.dataTransfer && event.dataTransfer.files;
    if (!files || !files.length) return;
    try {
      input.files = files;
    } catch (_) {
      return;
    }
    syncFileState();
  });

  form.addEventListener('submit', (event) => {
    if (!input.files || !input.files.length) {
      event.preventDefault();
      syncFileState();
      return;
    }
    submit.disabled = true;
    overlay.hidden = false;
  });

  window.addEventListener('pageshow', () => {
    overlay.hidden = true;
    syncFileState();
  });

  syncFileState();
})();
</script>"""

ROUTE_JS = """<script>
(() => {
  const routeRoot = document.querySelector('.route-page');
  const routeData = document.getElementById('route-data');
  const mapElement = document.getElementById('map');
  if (!routeRoot || !routeData || !mapElement || typeof L === 'undefined') return;

  const route = JSON.parse(routeData.textContent);
  const visits = route.visits || [];
  const geometry = route.geometry || [];
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const map = L.map('map', {zoomControl: true});
  const markers = new Map();
  const bounds = [];

  L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19,
    attribution: '© OpenStreetMap contributors'
  }).addTo(map);

  if (geometry.length) {
    const line = geometry.map((point) => [point[0], point[1]]);
    L.polyline(line, {weight: 5, opacity: 0.75}).addTo(map);
    line.forEach((point) => bounds.push(point));
  }

  const escapeHtml = (value) => String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  const formatMinute = (minute) => {
    const hours = String(Math.floor(minute / 60) % 24).padStart(2, '0');
    const minutes = String(minute % 60).padStart(2, '0');
    return `${hours}:${minutes}`;
  };

  const fitRoute = () => {
    map.invalidateSize(false);
    if (bounds.length) {
      map.fitBounds(bounds, {padding: [28, 28], animate: !reducedMotion});
    } else {
      map.setView([59.94, 30.31], 10, {animate: false});
    }
  };

  const setRouteView = (view, {fit = true} = {}) => {
    routeRoot.dataset.routeView = view;
    document.querySelectorAll('[data-route-switch]').forEach((button) => {
      const active = button.dataset.routeSwitch === view;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
    if (view === 'map') {
      requestAnimationFrame(() => {
        map.invalidateSize(false);
        if (fit) fitRoute();
      });
    }
  };

  visits.forEach((visit) => {
    const stop = visit.stop || {};
    const operation = stop.operation === 'pickup' ? 'pickup' : 'delivery';
    const reviewClass = stop.coord_status === 'review' ? ' review' : '';
    const icon = L.divIcon({
      className: 'leaflet-div-icon',
      html: `<div class="marker-num ${operation}${reviewClass}">${escapeHtml(visit.sequence)}</div>`,
      iconSize: [36, 36],
      iconAnchor: [18, 18]
    });
    const marker = L.marker([stop.lat, stop.lon], {icon}).addTo(map);
    const address = stop.address_normalized || stop.address_raw || '';
    marker.bindPopup(
      `<b>${escapeHtml(visit.sequence)}. ${escapeHtml(address)}</b><br>` +
      `ETA ${formatMinute(visit.arrival_min)}<br>` +
      `Заказ №${escapeHtml(stop.order_no)}`
    );
    marker.on('click', () => {
      setRouteView('list', {fit: false});
      requestAnimationFrame(() => {
        const card = document.getElementById(`stop-${visit.sequence}`);
        card?.scrollIntoView({behavior: reducedMotion ? 'auto' : 'smooth', block: 'center'});
      });
    });
    markers.set(String(visit.sequence), marker);
    bounds.push([stop.lat, stop.lon]);
  });

  document.querySelectorAll('[data-route-switch]').forEach((button) => {
    button.addEventListener('click', () => setRouteView(button.dataset.routeSwitch));
  });

  const focusStop = (card) => {
    const marker = markers.get(String(card.dataset.sequence));
    if (!marker) return;
    setRouteView('map', {fit: false});
    window.setTimeout(() => {
      map.invalidateSize(false);
      map.setView(marker.getLatLng(), 16, {animate: !reducedMotion});
      marker.openPopup();
    }, 40);
  };

  document.querySelectorAll('.stop[data-sequence]').forEach((card) => {
    card.addEventListener('click', (event) => {
      if (event.target.closest('a, button, summary, details, input, select, label')) return;
      focusStop(card);
    });
    card.addEventListener('keydown', (event) => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      event.preventDefault();
      focusStop(card);
    });
  });

  if (bounds.length) {
    map.fitBounds(bounds, {padding: [28, 28], animate: false});
  } else {
    map.setView([59.94, 30.31], 10, {animate: false});
  }

  setRouteView('list', {fit: false});
})();
</script>"""


def _shell(title: str, body: str, user: str | None = None, head: str = "") -> str:
    auth = (
        f'<div class="account"><span class="account-name">{html.escape(user)}</span>'
        f'<a class="top-link" href="/logout">Выйти</a></div>'
        if user else ""
    )
    top = (
        f'<header class="top"><a class="brand" href="/">Courier Router</a>{auth}</header>'
        if user else ""
    )
    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{html.escape(title)}</title>
{head}
<style>{CSS}</style>
</head>
<body>
{top}
<main class="wrap">{body}</main>
</body>
</html>"""


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_hex, digest_hex = encoded.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(digest_hex)),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _load_users() -> dict[str, str]:
    if not USERS_PATH.exists():
        return {}
    return json.loads(USERS_PATH.read_text(encoding="utf-8"))


def _save_users(users: dict[str, str]) -> None:
    USERS_PATH.parent.mkdir(parents=True, exist_ok=True)
    USERS_PATH.write_text(json.dumps(users, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        USERS_PATH.chmod(0o600)
    except OSError:
        pass


def add_user(username: str, password: str | None = None) -> None:
    username = username.strip()
    if not username or any(ch in username for ch in "/\\\0"):
        raise ValueError("Некорректное имя пользователя")
    if password is None:
        password = getpass.getpass("Пароль: ")
        repeated = getpass.getpass("Повторите пароль: ")
        if password != repeated:
            raise ValueError("Пароли не совпадают")
    if len(password) < 8:
        raise ValueError("Пароль должен быть не короче 8 символов")
    users = _load_users()
    users[username] = _hash_password(password)
    _save_users(users)
    print(f"Пользователь {username!r} сохранён в {USERS_PATH}")


def list_users() -> None:
    users = _load_users()
    if not users:
        print("Пользователей пока нет")
        return
    for username in sorted(users, key=str.casefold):
        print(username)


def _require_user(request: Request) -> str | None:
    user = request.session.get("user")
    return str(user) if user else None


def _user_runs_root(user: str) -> Path:
    """Stable filesystem namespace for one account without putting usernames in paths."""
    user_key = hashlib.sha256(user.encode("utf-8")).hexdigest()[:32]
    return RUNS_ROOT / "_users" / user_key


def _owned_meta(folder: Path, user: str) -> dict | None:
    try:
        meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return meta if meta.get("uploaded_by") == user else None


def _find_run_folder(user: str, run_id: str) -> Path | None:
    """Find a run owned by user. New namespaced layout first, then legacy v1 layout."""
    if not run_id.isalnum():
        return None
    candidates = [
        _user_runs_root(user) / run_id,
        RUNS_ROOT / run_id,
    ]
    for folder in candidates:
        if folder.is_dir() and _owned_meta(folder, user) is not None:
            return folder
    return None


def _plain_error(error: str) -> str:
    return f'<div class="error" role="alert"><div class="error-summary">{html.escape(error)}</div></div>' if error else ""


def _build_error(error: str) -> str:
    if not error:
        return ""
    raw = str(error).strip()
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    summary = lines[0] if lines else raw
    for line in lines:
        if "ERROR:" in line:
            summary = line[line.index("ERROR:"):]
            break
    details = ""
    if len(lines) > 1 or summary != raw:
        details = (
            '<details><summary>Подробности</summary>'
            f'<pre>{html.escape(raw)}</pre></details>'
        )
    return (
        '<div class="error" role="alert">'
        f'<div class="error-summary">{html.escape(summary)}</div>{details}</div>'
    )


def _login_page(error: str = "") -> str:
    err = _plain_error(error)
    body = f"""<div class="login card">
<span class="login-badge">Доступ через VPN</span>
<h1>Courier Router</h1>
<p class="lead">Закрытый планировщик маршрутов доставки.</p>
{err}
<form method="post" action="/login">
  <div class="field">
    <label for="username">Логин</label>
    <input id="username" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required autofocus>
  </div>
  <div class="field">
    <label for="password">Пароль</label>
    <input id="password" type="password" name="password" autocomplete="current-password" required>
  </div>
  <button class="btn" type="submit">Войти</button>
</form>
</div>"""
    return _shell("Вход", body)


def _list_runs(user: str) -> list[dict]:
    if not RUNS_ROOT.exists():
        return []

    items: list[dict] = []
    seen: set[str] = set()

    def append_run(folder: Path, meta: dict) -> None:
        route_path = folder / "route.json"
        if not route_path.exists():
            return
        try:
            route = json.loads(route_path.read_text(encoding="utf-8"))
            summary = route.get("summary", {})
            visits = route.get("visits", [])
            review_count = sum(
                1 for visit in visits
                if visit.get("stop", {}).get("coord_status") == "review"
            )
            items.append({
                "id": folder.name,
                "meta": meta,
                "summary": summary,
                "count": len(visits),
                "review_count": review_count,
                "mtime": folder.stat().st_mtime,
            })
        except (OSError, ValueError, TypeError):
            return

    user_root = _user_runs_root(user)
    if user_root.exists():
        for folder in user_root.iterdir():
            if not folder.is_dir():
                continue
            meta = _owned_meta(folder, user)
            if meta is None:
                continue
            append_run(folder, meta)
            seen.add(folder.name)

    for folder in RUNS_ROOT.iterdir():
        if not folder.is_dir() or folder.name.startswith("_") or folder.name in seen:
            continue
        meta = _owned_meta(folder, user)
        if meta is None:
            continue
        append_run(folder, meta)

    return sorted(items, key=lambda x: x["mtime"], reverse=True)[:30]


def _plural_ru(number: int, one: str, few: str, many: str) -> str:
    value = abs(int(number)) % 100
    if 11 <= value <= 14:
        return many
    value %= 10
    if value == 1:
        return one
    if 2 <= value <= 4:
        return few
    return many


def _home_page(user: str, error: str = "") -> str:
    err = _build_error(error)
    runs_html = ""
    for run in _list_runs(user):
        km = run["summary"].get("total_distance_m", 0) / 1000
        rid = html.escape(str(run["id"]))
        d = html.escape(run["meta"].get("date", ""))
        depart = html.escape(run["meta"].get("depart", ""))
        review_count = int(run.get("review_count", 0))
        status = (
            f'<span class="status-chip review">⚠ {review_count} на сверку</span>'
            if review_count else '<span class="status-chip ok">✓ ок</span>'
        )
        runs_html += f"""<div class="run">
<div class="run-main">
  <div class="run-title-row"><span class="run-title">{d}</span>{status}</div>
  <div class="run-meta">{run['count']} {_plural_ru(run['count'], 'точка', 'точки', 'точек')} · {km:.1f} км · старт {depart}</div>
</div>
<div class="run-actions">
  <a class="btn secondary" href="/routes/{rid}">Открыть</a>
  <details class="more-menu">
    <summary class="icon-btn" aria-label="Действия с маршрутом">···</summary>
    <div class="menu-popover">
      <form method="post" action="/routes/{rid}/delete" onsubmit="return confirm('Удалить маршрут за {d}? Отменить нельзя.')">
        <button class="menu-danger" type="submit">Удалить маршрут</button>
      </form>
    </div>
  </details>
</div>
</div>"""
    if not runs_html:
        runs_html = '<div class="empty-state">Пока нет рассчитанных маршрутов.</div>'
    today = date.today().isoformat()
    body = f"""{err}
<section class="card">
  <h1>Новый маршрут</h1>
  <p class="lead">Загрузите XLSX или CSV с заказами. После запуска расчёт может занять до минуты.</p>
  <form id="route-form" method="post" action="/routes" enctype="multipart/form-data">
    <label class="upload" id="drop-zone" for="route-file">
      <input class="file-input" id="route-file" type="file" name="table" accept=".xlsx,.csv" required>
      <span class="upload-content">
        <span class="upload-icon" aria-hidden="true">↑</span>
        <span class="upload-title">Выберите таблицу</span>
        <span class="upload-hint">Нажмите сюда или перетащите файл</span>
        <span class="upload-file-name" id="selected-file">Файл пока не выбран</span>
      </span>
    </label>
    <div class="form-grid">
      <div class="field">
        <label for="route-day">Дата</label>
        <input id="route-day" type="date" name="day" value="{today}" required>
      </div>
      <div class="field">
        <label for="route-depart">Старт</label>
        <input id="route-depart" type="time" name="depart" value="10:00" required>
      </div>
      <div class="field">
        <label for="route-end">Финиш</label>
        <select id="route-end" name="end">
          <option value="open" selected>Последняя точка</option>
          <option value="depot">Вернуться на базу</option>
        </select>
      </div>
    </div>
    <label class="check-row"><input type="checkbox" name="allow_low_confidence" value="1">Разрешить точки с низкой точностью геокодирования</label>
    <div class="toolbar"><button class="btn" id="build-submit" type="submit" disabled>Построить маршрут</button></div>
  </form>
</section>
<section class="card">
  <h2>Мои последние маршруты</h2>
  <div class="run-list">{runs_html}</div>
</section>
<div class="build-overlay" id="build-overlay" role="status" aria-live="polite" aria-busy="true" hidden>
  <div class="build-overlay-card">
    <div class="spinner" aria-hidden="true"></div>
    <h2>Строю маршрут, это займёт до минуты</h2>
    <p class="muted">Не закрывайте страницу.</p>
  </div>
</div>""" + HOME_JS
    return _shell("Маршруты", body, user)


def _hhmm(minute: int) -> str:
    return f"{(minute // 60) % 24:02d}:{minute % 60:02d}"


def _route_page(user: str, run_id: str, meta: dict, route: dict) -> str:
    if not route.get("feasible"):
        warnings = route.get("warnings", [])
        warnings_html = "".join(f"<p>{html.escape(str(item))}</p>" for item in warnings)
        if not warnings_html:
            warnings_html = "<p>Не удалось составить маршрут с текущими ограничениями.</p>"
        body = f"""<section class="card">
<h1>Маршрут не построен</h1>
<p class="lead">Проверьте исходные данные и ограничения по времени.</p>
<div class="error" role="alert">{warnings_html}</div>
<a class="btn secondary" href="/">← К загрузке</a>
</section>"""
        return _shell("Маршрут не построен", body, user)

    summary = route.get("summary", {})
    visits = route.get("visits", [])
    km = summary.get("total_distance_m", 0) / 1000
    travel = round(summary.get("total_travel_sec", 0) / 60)
    review_count = sum(1 for visit in visits if visit.get("stop", {}).get("coord_status") == "review")
    stops_html = ""

    for visit in visits:
        stop = visit["stop"]
        lat, lon = stop["lat"], stop["lon"]
        address = stop.get("address_normalized") or stop.get("address_raw") or ""
        nav = html.escape(f"https://yandex.ru/maps/?rtext=~{lat}%2C{lon}&rtt=auto", quote=True)
        is_pickup = stop.get("operation") == "pickup"
        operation = "Забор" if is_pickup else "Доставка"
        operation_class = "pickup" if is_pickup else "delivery"
        phone_raw = stop.get("phone") or ""
        phone = html.escape(phone_raw)
        phone_html = (
            f'<p><a class="phone-link" href="tel:{quote(phone_raw)}">☎ {phone}</a></p>'
            if phone else ""
        )
        window = html.escape(stop.get("window") or "нет")
        payment = html.escape(stop.get("payment") or "")
        comment = html.escape(stop.get("comment") or "")
        raw_addr = stop.get("address_raw") or ""
        raw_html = (
            f'<p class="raw-address">в таблице: {html.escape(raw_addr)}</p>'
            if raw_addr and raw_addr.strip().casefold() != address.strip().casefold() else ""
        )
        review = stop.get("coord_status") == "review"
        review_pill = '<span class="pill review">⚠ проверить координаты</span>' if review else ""
        note = html.escape(stop.get("coord_note") or "")
        note_html = f'<p class="coord-note">{note}</p>' if review and note else ""
        warnings_html = "".join(
            f'<p class="stop-warning">⚠ {html.escape(str(warning))}</p>'
            for warning in stop.get("warnings", []) if warning
        )
        comment_html = (
            f'<p class="stop-comment"><span class="muted">Комментарий:</span> {comment}</p>'
            if comment else ""
        )
        late_by = int(visit.get("late_by_min", 0) or 0)
        late_html = f'<span class="pill review">опоздание {late_by} мин</span>' if late_by > 0 else ""
        stops_html += f"""<article class="stop" id="stop-{visit['sequence']}" data-sequence="{visit['sequence']}" tabindex="0" role="button" aria-label="Показать точку {visit['sequence']} на карте">
  <div class="stop-heading-row">
    <span class="seq {operation_class}">{visit['sequence']}</span>
    <h3 class="stop-address">{html.escape(address)}</h3>
  </div>
  {raw_html}
  <div class="pill-row">
    <span class="pill neutral">ETA {_hhmm(visit['arrival_min'])}</span>
    <span class="pill {operation_class}">{operation}</span>
    {review_pill}
    {late_html}
  </div>
  {note_html}
  {warnings_html}
  <div class="stop-meta">
    <p>Заказ №{html.escape(str(stop.get('order_no', '')))} · окно {window}</p>
    {phone_html}
    {f'<p>Оплата: {payment}</p>' if payment else ''}
    <p class="muted">От предыдущей: {visit['distance_m_from_prev'] / 1000:.1f} км · {round(visit['travel_sec_from_prev'] / 60)} мин</p>
  </div>
  {comment_html}
  <div class="stop-actions"><a class="btn secondary" href="{nav}" target="_blank" rel="noopener">Открыть в Яндекс Картах</a></div>
</article>"""

    data = json.dumps(route, ensure_ascii=False).replace("</", "<\\/")

    # Одна ссылка на весь маршрут для приложения «Яндекс Карты / Навигатор» на iOS.
    navi_url = ""
    geom = route.get("geometry") or []
    if visits and geom:
        from .navlinks import RoutePoint, build_yandex_url
        d_lat, d_lon = geom[0][0], geom[0][1]
        pts = [RoutePoint("start", "База", d_lat, d_lon)]
        for visit in visits:
            stop = visit["stop"]
            pts.append(RoutePoint(
                "via",
                stop.get("address_normalized") or stop.get("address_raw") or "",
                stop["lat"],
                stop["lon"],
                str(stop.get("order_no", "")),
            ))
        if meta.get("end", "depot") == "depot":
            pts.append(RoutePoint("finish", "База", d_lat, d_lon))
        if len(pts) >= 2:
            navi_url = build_yandex_url(pts)

    review_status = (
        f'<span class="status-chip review">⚠ {review_count} на сверку</span>'
        if review_count else '<span class="status-chip ok">✓ координаты без пометок</span>'
    )
    route_class = "route-page has-bottom-action" if navi_url else "route-page"
    safe_navi_url = html.escape(navi_url, quote=True)
    navigator_note = (
        '<p class="navigator-note">Главная кнопка закреплена снизу, чтобы маршрут можно было открыть одной рукой.</p>'
        if navi_url else ""
    )
    bottom_action = (
        f'<div class="bottom-action"><div class="bottom-action-inner"><a class="btn" href="{safe_navi_url}">🧭 Открыть в Навигаторе</a></div></div>'
        if navi_url else ""
    )

    head = '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>'
    body = f"""<div class="{route_class}" data-route-view="list">
<section class="card">
  <div class="summary-head">
    <div>
      <h1>Маршрут на {html.escape(meta.get('date', ''))}</h1>
      <div class="route-meta">Старт {html.escape(meta.get('depart', ''))} · {'возврат на базу' if meta.get('end', 'depot') == 'depot' else 'финиш на последней точке'}</div>
    </div>
    <details class="more-menu">
      <summary class="icon-btn" aria-label="Действия с маршрутом">···</summary>
      <div class="menu-popover">
        <form method="post" action="/routes/{run_id}/delete" onsubmit="return confirm('Удалить этот маршрут? Отменить нельзя.')">
          <button class="menu-danger" type="submit">Удалить маршрут</button>
        </form>
      </div>
    </details>
  </div>
  <div class="metric-grid">
    <div class="metric"><span class="metric-label">Пробег</span><strong class="metric-value">{km:.1f} км</strong></div>
    <div class="metric"><span class="metric-label">Время в пути</span><strong class="metric-value">{travel} мин</strong></div>
  </div>
  <div class="route-status-line"><span>{len(visits)} {_plural_ru(len(visits), 'точка', 'точки', 'точек')}, из них {review_count} на сверку</span>{review_status}</div>
  {navigator_note}
  <div class="route-summary-actions"><a class="btn secondary" href="/">← К загрузке</a></div>
</section>
<div class="view-switch" aria-label="Режим просмотра">
  <button class="segment-btn is-active" type="button" data-route-switch="list" aria-pressed="true">Список</button>
  <button class="segment-btn" type="button" data-route-switch="map" aria-pressed="false">Карта</button>
</div>
<div class="route-layout">
  <div class="map-shell card" data-route-pane="map"><div id="map" class="map" aria-label="Карта маршрута"></div></div>
  <div class="stops" data-route-pane="list">{stops_html}</div>
</div>
<script id="route-data" type="application/json">{data}</script>
{bottom_action}
</div>""" + ROUTE_JS
    return _shell(f"Маршрут {meta.get('date', '')}", body, user, head)


def create_app(session_secret: str | None = None) -> FastAPI:
    secret = session_secret or os.getenv("WEB_SESSION_SECRET")
    if not secret:
        raise RuntimeError("Укажите WEB_SESSION_SECRET (например: openssl rand -hex 32)")
    app = FastAPI(title="Courier Router Web", docs_url=None, redoc_url=None)
    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        same_site="lax",
        https_only=os.getenv("WEB_HTTPS_ONLY", "1") != "0",
        max_age=60 * 60 * 24 * 30,
    )

    @app.get("/login", response_class=HTMLResponse)
    async def login_get(request: Request):
        if _require_user(request):
            return RedirectResponse("/", status_code=303)
        return HTMLResponse(_login_page())

    @app.post("/login", response_class=HTMLResponse)
    async def login_post(request: Request, username: str = Form(...), password: str = Form(...)):
        encoded = _load_users().get(username)
        if not encoded or not _verify_password(password, encoded):
            return HTMLResponse(_login_page("Неверный логин или пароль"), status_code=401)
        request.session.clear()
        request.session["user"] = username
        return RedirectResponse("/", status_code=303)

    @app.get("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        return HTMLResponse(_home_page(user))

    @app.post("/routes", response_class=HTMLResponse)
    async def build_route(
        request: Request,
        table: UploadFile = File(...),
        day: str = Form(...),
        depart: str = Form(...),
        end: str = Form("open"),
        allow_low_confidence: str | None = Form(None),
    ):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        suffix = Path(table.filename or "").suffix.lower()
        if suffix not in {".xlsx", ".csv"}:
            return HTMLResponse(_home_page(user, "Поддерживаются только XLSX и CSV"), status_code=400)
        payload = await table.read(MAX_UPLOAD_BYTES + 1)
        if len(payload) > MAX_UPLOAD_BYTES:
            return HTMLResponse(_home_page(user, f"Файл больше лимита {MAX_UPLOAD_BYTES // 1024 // 1024} МБ"), status_code=413)
        run_id = uuid4().hex[:12]
        folder = _user_runs_root(user) / run_id
        folder.mkdir(parents=True, exist_ok=False)
        input_path = folder / f"input{suffix}"
        input_path.write_bytes(payload)
        meta = {"date": day, "depart": depart, "end": end, "uploaded_by": user, "filename": table.filename}
        (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        args = SimpleNamespace(
            xlsx=str(input_path), date=day, depart=depart, end=end,
            output=str(folder), allow_low_confidence=bool(allow_low_confidence),
        )
        try:
            await run_in_threadpool(cmd_plan, args)
        except Exception as exc:
            return HTMLResponse(_home_page(user, f"Не удалось построить маршрут: {exc}"), status_code=400)
        return RedirectResponse(f"/routes/{run_id}", status_code=303)

    @app.get("/routes/{run_id}", response_class=HTMLResponse)
    async def route_view(request: Request, run_id: str):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not run_id.isalnum():
            return HTMLResponse("Некорректный маршрут", status_code=400)
        folder = _find_run_folder(user, run_id)
        if folder is None:
            return HTMLResponse("Маршрут не найден", status_code=404)
        try:
            meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
            route = json.loads((folder / "route.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return HTMLResponse("Маршрут не найден", status_code=404)
        return HTMLResponse(_route_page(user, run_id, meta, route))

    @app.post("/routes/{run_id}/delete")
    async def delete_route(request: Request, run_id: str):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if run_id.isalnum():
            folder = _find_run_folder(user, run_id)   # уже проверяет владельца по meta.uploaded_by
            if folder is not None:
                shutil.rmtree(folder, ignore_errors=True)
        return RedirectResponse("/", status_code=303)

    @app.get("/routes/{run_id}/itinerary", response_class=HTMLResponse)
    async def itinerary(request: Request, run_id: str):
        user = _require_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        if not run_id.isalnum():
            return HTMLResponse("Некорректный маршрут", status_code=400)
        folder = _find_run_folder(user, run_id)
        if folder is None:
            return HTMLResponse("Маршрут не найден", status_code=404)
        path = folder / "itinerary.txt"
        if not path.exists():
            return HTMLResponse("Маршрут не найден", status_code=404)
        text = html.escape(path.read_text(encoding="utf-8"))
        return HTMLResponse(_shell("Маршрут текстом", f'<div class="card"><pre style="white-space:pre-wrap;font:inherit">{text}</pre></div>', user))

    @app.get("/health")
    async def health():
        return {"ok": True}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(prog="courier-web")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Запустить веб-интерфейс")
    serve.add_argument("--host", default=os.getenv("WEB_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=int(os.getenv("WEB_PORT", "8080")))
    user = sub.add_parser("user-add", help="Создать или сменить пароль пользователя")
    user.add_argument("username")
    sub.add_parser("user-list", help="Показать созданные аккаунты")
    args = parser.parse_args()
    if args.command == "user-add":
        add_user(args.username)
        return
    if args.command == "user-list":
        list_users()
        return
    uvicorn.run(create_app(), host=args.host, port=args.port, proxy_headers=True)


if __name__ == "__main__":
    main()
