import { useEffect, useState } from "react";
import { api } from "../../api/client";
import RuleEditor from "./RuleEditor";

export interface AutomationRule {
  id: string;
  name: string;
  description: string;
  trigger_type: string;
  trigger_config: Record<string, unknown>;
  action_type: string;
  action_config: Record<string, unknown>;
  enabled: boolean;
}

export interface Person {
  id: string;
  name: string;
  role: string;
}

const TRIGGER_LABEL: Record<string, string> = {
  person_enters: "When person enters",
  person_exits: "When person exits",
  schedule: "At scheduled time",
  voice_command: "On voice command",
};

const ACTION_LABEL: Record<string, string> = {
  gpio: "GPIO output",
  tts: "Speak response",
  notification: "Send notification",
  command: "Run shell command",
};

function RuleCard({
  rule,
  personNames,
  onEdit,
  onToggle,
  onDelete,
}: {
  rule: AutomationRule;
  personNames: Record<string, string>;
  onEdit: () => void;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const describeTrigger = () => {
    const cfg = rule.trigger_config;
    switch (rule.trigger_type) {
      case "person_enters":
      case "person_exits": {
        const parts: string[] = [];
        if (cfg.person_id)
          parts.push(personNames[cfg.person_id as string] || String(cfg.person_id));
        if (cfg.role) parts.push(`any ${cfg.role}`);
        return parts.length ? parts.join(", ") : "any person";
      }
      case "schedule":
        return `${cfg.time || "—"}${
          Array.isArray(cfg.days) && cfg.days.length
            ? ` (${(cfg.days as string[]).join(", ")})`
            : ""
        }`;
      case "voice_command":
        return `"${cfg.phrase || "—"}"`;
      default:
        return "";
    }
  };

  const describeAction = () => {
    const cfg = rule.action_config;
    switch (rule.action_type) {
      case "gpio":
        return `Pin ${cfg.pin ?? "?"} → ${cfg.state ?? "?"}`;
      case "tts":
        return `Say: "${String(cfg.text ?? "").slice(0, 40)}"`;
      case "notification":
        return String(cfg.message ?? "").slice(0, 60);
      case "command":
        return String(cfg.shell ?? "").slice(0, 60);
      default:
        return "";
    }
  };

  return (
    <div
      className={`bg-white rounded-xl border p-5 transition-opacity ${
        rule.enabled ? "border-gray-200" : "border-gray-200 opacity-60"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="text-base font-semibold truncate">{rule.name}</h3>
            {!rule.enabled && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-gray-100 text-gray-500">
                Disabled
              </span>
            )}
          </div>
          {rule.description && (
            <p className="text-sm text-gray-500 mt-0.5">{rule.description}</p>
          )}
        </div>
        <button
          onClick={onToggle}
          className={`relative inline-flex h-6 w-11 flex-shrink-0 items-center rounded-full transition-colors ${
            rule.enabled ? "bg-indigo-500" : "bg-gray-300"
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              rule.enabled ? "translate-x-6" : "translate-x-1"
            }`}
          />
        </button>
      </div>

      <div className="mt-3 space-y-1.5 text-xs">
        <div className="flex gap-2">
          <span className="text-gray-500 w-20 flex-shrink-0">Trigger:</span>
          <span className="text-gray-900">
            <span className="font-medium">
              {TRIGGER_LABEL[rule.trigger_type] || rule.trigger_type}
            </span>
            {" — "}
            <span className="text-gray-700">{describeTrigger()}</span>
          </span>
        </div>
        <div className="flex gap-2">
          <span className="text-gray-500 w-20 flex-shrink-0">Action:</span>
          <span className="text-gray-900">
            <span className="font-medium">
              {ACTION_LABEL[rule.action_type] || rule.action_type}
            </span>
            {" — "}
            <span className="text-gray-700 font-mono">{describeAction()}</span>
          </span>
        </div>
      </div>

      <div className="mt-4 flex gap-2 pt-3 border-t border-gray-100">
        <button
          onClick={onEdit}
          className="text-xs px-3 py-1 rounded border border-gray-200 hover:bg-gray-50"
        >
          Edit
        </button>
        <button
          onClick={onDelete}
          className="text-xs px-3 py-1 rounded border border-red-200 text-red-600 hover:bg-red-50"
        >
          Delete
        </button>
      </div>
    </div>
  );
}

export default function AutomationPage() {
  const [rules, setRules] = useState<AutomationRule[]>([]);
  const [persons, setPersons] = useState<Person[]>([]);
  const [editingRule, setEditingRule] = useState<AutomationRule | null>(null);
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    try {
      const [rulesData, personsData] = await Promise.all([
        api.get<{ rules: AutomationRule[] }>("/automation"),
        api.get<{ persons: Person[] }>("/system/persons"),
      ]);
      setRules(rulesData.rules);
      setPersons(personsData.persons);
    } catch {
      // Ignore
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const personNames = Object.fromEntries(persons.map((p) => [p.id, p.name]));

  const toggleRule = async (rule: AutomationRule) => {
    await api.put(`/automation/${rule.id}`, { enabled: !rule.enabled });
    load();
  };

  const deleteRule = async (rule: AutomationRule) => {
    if (!confirm(`Delete rule "${rule.name}"?`)) return;
    await api.delete(`/automation/${rule.id}`);
    load();
  };

  const saveRule = async (rule: Partial<AutomationRule>) => {
    if (editingRule) {
      await api.put(`/automation/${editingRule.id}`, rule);
    } else {
      await api.post("/automation", rule);
    }
    setCreating(false);
    setEditingRule(null);
    load();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Automation Rules
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            {rules.length} rule{rules.length === 1 ? "" : "s"} configured
          </p>
        </div>
        <button
          onClick={() => setCreating(true)}
          className="px-4 py-2 rounded-lg bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700"
        >
          + New Rule
        </button>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-400 animate-pulse">
          Loading rules...
        </div>
      ) : rules.length === 0 ? (
        <div className="bg-white rounded-xl border border-gray-200 p-12 text-center">
          <p className="text-gray-400 text-sm">
            No automation rules yet. Click "New Rule" to create one.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {rules.map((rule) => (
            <RuleCard
              key={rule.id}
              rule={rule}
              personNames={personNames}
              onEdit={() => setEditingRule(rule)}
              onToggle={() => toggleRule(rule)}
              onDelete={() => deleteRule(rule)}
            />
          ))}
        </div>
      )}

      {(creating || editingRule) && (
        <RuleEditor
          rule={editingRule}
          persons={persons}
          onSave={saveRule}
          onCancel={() => {
            setCreating(false);
            setEditingRule(null);
          }}
        />
      )}
    </div>
  );
}
