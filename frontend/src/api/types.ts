export interface ServiceStatus {
  name: string;
  healthy: boolean;
  uptime_seconds: number;
  details: Record<string, unknown>;
}

export interface SystemStatus {
  privacy_mode: boolean;
  services: Record<string, string>;
  timestamp: number;
}

export interface Person {
  id: string;
  name: string;
  role: "teacher" | "student" | "admin" | "guest";
}

export interface AttendanceData {
  present: Person[];
  count: number;
}

export interface EventRecord {
  id: number;
  type: string;
  person_id: string | null;
  session_id: string | null;
  data: string;
  created_at: string;
}

export interface HealthStatus {
  status: string;
  uptime_seconds: number;
  ws_clients: number;
}

export interface WebSocketMessage {
  channel: string;
  data: Record<string, unknown>;
}
