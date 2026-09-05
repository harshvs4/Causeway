// Mirrors engine/models.py. If the Fact contract changes, this changes with it.
export type Confidence = "verified" | "derived" | "scenario";

export interface Source {
  file: string;
  row_ref: string;
  fields: string[];
}

export interface Fact {
  fact_id: string;
  client_id: string;
  kind: string;
  headline: string;
  detail: string;
  numbers: Record<string, number>;
  sources: Source[];
  as_of: string;
  confidence: Confidence;
  severity: number;
}

export interface Envelope {
  schema_version: number;
  generated_at: string;
  as_of: string;
  clients: string[];
  fact_count: number;
  facts: Fact[];
  source_rows: Record<string, Record<string, string | number | null>>;
}

export const sourceKey = (s: Source) => `${s.file}::${s.row_ref}`;
