export interface User {
  user_id: string;
  name: string;
  email: string;
  organization?: string;
  api_key: string;
}

export interface Session {
  session_id: string;
  user_id: string;
  status: 'queued' | 'running' | 'complete' | 'failed';
  progress_pct: number;
  patient_id?: string;
  vcf_filename?: string;
  analysis_mode: 'solo' | 'trio';
  genome_build: string;
  variant_count?: number;
  classifications?: {
    P: number;
    LP: number;
    VUS: number;
    LB: number;
    B: number;
  };
  denovo_count?: number;
  compound_het_count?: number;
  created_at: string;
  started_at?: string;
  completed_at?: string;
  error_message?: string;
}

export interface Variant {
  variant_id: string;
  chrom: string;
  pos: number;
  ref: string;
  alt: string;
  gene?: string;
  consequence?: string;
  classification: 'P' | 'LP' | 'VUS' | 'LB' | 'B';
  acmg_criteria: string[];
  evidence_summary?: string;
}

export interface QCResult {
  id: string;
  session_id: string;
  patient_id?: string;
  analysis_mode: string;
  qc_status: 'PASS' | 'WARNING' | 'FAIL';
  qc_score: number;
  confidence: number;
  input_qc: string;
  annotation_qc: string;
  evidence_qc: string;
  classification_qc: string;
  report_qc: string;
  issues: string[];
  created_at: string;
}

export interface DashboardStats {
  total_analyses: number;
  completed: number;
  running: number;
  failed: number;
  total_variants: number;
  classification_distribution: {
    P: number;
    LP: number;
    VUS: number;
    LB: number;
    B: number;
  };
}

export interface Chat {
  chat_id: string;
  user_id: string;
  title: string;
  messages: ChatMessage[];
  created_at: string;
  updated_at: string;
}

export interface ChatMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  file_info?: {
    filename: string;
    file_type: string;
    file_path: string;
  };
}

export interface ProgressEvent {
  stage: string;
  progress: number;
  message: string;
  variant_id?: string;
  gene?: string;
  timestamp: string;
}
