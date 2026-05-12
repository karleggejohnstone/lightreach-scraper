-- Migration: lightreach_audit_events
-- Run this once in the Supabase SQL Editor before deploying the writer.

create table if not exists lightreach_audit_events (
  id bigserial primary key,
  project_id text not null,
  customer_name text,
  event_timestamp timestamptz,
  actor text,
  actor_type text,
  event_type text,
  details jsonb,
  raw_line text,
  scraped_at timestamptz default now(),
  unique (project_id, event_timestamp, raw_line)
);

create index if not exists idx_lr_events_project on lightreach_audit_events (project_id);
create index if not exists idx_lr_events_type on lightreach_audit_events (event_type);
create index if not exists idx_lr_events_timestamp on lightreach_audit_events (event_timestamp desc);
create index if not exists idx_lr_events_project_timestamp on lightreach_audit_events (project_id, event_timestamp);

create table if not exists lightreach_project_snapshot (
  project_id text primary key,
  customer_name text,
  ntp_status text,
  install_status text,
  domestic_content_status text,
  credit_result text,
  ntp_submitted_at timestamptz,
  ntp_approved_at timestamptz,
  ntp_rejected_at timestamptz,
  contract_signed_at timestamptz,
  customer_account_created_at timestamptz,
  payment_method_added_at timestamptz,
  install_pkg_submitted_at timestamptz,
  install_submitted_at timestamptz,
  credit_inquiry_at timestamptz,
  open_stipulations_count int default 0,
  cleared_stipulations_count int default 0,
  oldest_open_stip_flagged_at timestamptz,
  doc_types_uploaded text[],
  doc_types_approved text[],
  total_event_count int default 0,
  scraped_at timestamptz default now()
);

create index if not exists idx_lr_snapshot_ntp_status on lightreach_project_snapshot (ntp_status);
create index if not exists idx_lr_snapshot_install_status on lightreach_project_snapshot (install_status);
