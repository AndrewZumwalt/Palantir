import {
  Clock,
  Cpu,
  DoorOpen,
  LogOut,
  Megaphone,
  Mic,
  Pencil,
  Plus,
  Terminal,
  Trash2,
  Volume2,
  Workflow,
  Zap,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { ComponentType } from "react";
import { api } from "../../api/client";
import { Button } from "../ui/Button";
import { EmptyState, LoadingLines } from "../ui/EmptyState";
import { Toggle } from "../ui/Field";
import { Panel } from "../ui/Panel";
import { StatusPill } from "../ui/StatusPill";
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

const TRIGGER_META: Record<
  string,
  { label: string; icon: ComponentType<{ className?: string }> }
> = {
  person_enters: { label: "SUBJECT ENTERS", icon: DoorOpen },
  person_exits: { label: "SUBJECT EXITS", icon: LogOut },
  schedule: { label: "ON SCHEDULE", icon: Clock },
  voice_command: { label: "VOICE CMD", icon: Mic },
};

const ACTION_META: Record<
  string,
  { label: string; icon: ComponentType<{ className?: string }> }
> = {
  gpio: { label: "GPIO PULSE", icon: Cpu },
  tts: { label: "SPEAK", icon: Volume2 },
  notification: { label: "NOTIFY", icon: Megaphone },
  command: { label: "SHELL", icon: Terminal },
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
  const TriggerIcon = TRIGGER_META[rule.trigger_type]?.icon ?? Zap;
  const ActionIcon = ACTION_META[rule.action_type]?.icon ?? Zap;

  const describeTrigger = () => {
    const cfg = rule.trigger_config;
    switch (rule.trigger_type) {
      case "person_enters":
      case "person_exits": {
        const parts: string[] = [];
        if (cfg.person_id)
          parts.push(
            personNames[cfg.person_id as string] || String(cfg.person_id)
          );
        if (cfg.role) parts.push(`any ${cfg.role}`);
        return parts.length ? parts.join(", ") : "any subject";
      }
      case "schedule":
        return `${cfg.time || "—"}${
          Array.isArray(cfg.days) && cfg.days.length
            ? ` · ${(cfg.days as string[]).join(", ")}`
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
        return `pin ${cfg.pin ?? "?"} → ${cfg.state ?? "?"}`;
      case "tts":
        return `"${String(cfg.text ?? "").slice(0, 48)}"`;
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
      className={[
        "group relative bg-[#0a0f1c] border transition-all",
        rule.enabled
          ? "border-[#1c2540] hover:border-amber-700/50"
          : "border-[#141d35] opacity-60 hover:opacity-80",
      ].join(" ")}
    >
      {/* Active stripe */}
      {rule.enabled && (
        <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-amber-500" />
      )}

      <div className="p-4 pl-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="text-base font-semibold text-gray-100 truncate">
                {rule.name}
              </h3>
              {!rule.enabled && (
                <StatusPill tone="gray" size="xs">
                  DISABLED
                </StatusPill>
              )}
            </div>
            {rule.description && (
              <p className="text-xs text-gray-500 mt-1">{rule.description}</p>
            )}
          </div>
          <Toggle checked={rule.enabled} onChange={onToggle} />
        </div>

        {/* Trigger / Action diagram */}
        <div className="mt-4 grid grid-cols-[1fr_auto_1fr] items-center gap-2 font-data text-[11px]">
          <div className="bg-[#05080f] border border-[#1c2540] px-2.5 py-2 min-w-0">
            <div className="flex items-center gap-1.5 text-cyan-400 uppercase tracking-[0.14em] text-[10px]">
              <TriggerIcon className="w-3 h-3" />
              {TRIGGER_META[rule.trigger_type]?.label ?? rule.trigger_type}
            </div>
            <div className="text-gray-300 truncate mt-0.5">
              {describeTrigger()}
            </div>
          </div>
          <div className="text-amber-500 text-lg select-none">›</div>
          <div className="bg-[#05080f] border border-[#1c2540] px-2.5 py-2 min-w-0">
            <div className="flex items-center gap-1.5 text-amber-400 uppercase tracking-[0.14em] text-[10px]">
              <ActionIcon className="w-3 h-3" />
              {ACTION_META[rule.action_type]?.label ?? rule.action_type}
            </div>
            <div className="text-gray-300 truncate mt-0.5">
              {describeAction()}
            </div>
          </div>
        </div>

        <div className="mt-4 flex gap-1.5 pt-3 border-t border-[#141d35]">
          <Button
            size="sm"
            variant="ghost"
            onClick={onEdit}
            iconLeft={<Pencil className="w-3 h-3" />}
          >
            EDIT
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={onDelete}
            iconLeft={<Trash2 className="w-3 h-3" />}
            className="text-red-400 hover:bg-red-500/10"
          >
            DELETE
          </Button>
        </div>
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
      // ignore
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

  const activeCount = rules.filter((r) => r.enabled).length;

  return (
    <div className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <div className="font-data text-[10px] uppercase tracking-[0.24em] text-amber-500">
            // DIRECTIVES
          </div>
          <h1 className="text-2xl md:text-3xl font-semibold text-gray-100 mt-1">
            Automation rulebook
          </h1>
          <p className="text-sm text-gray-500 mt-1 max-w-2xl">
            When <strong className="text-amber-400">trigger</strong> happens,
            execute <strong className="text-cyan-400">action</strong>. Rules
            fire locally with no external dependencies.
          </p>
        </div>
        <Button
          variant="primary"
          size="md"
          iconLeft={<Plus className="w-4 h-4" />}
          onClick={() => setCreating(true)}
        >
          NEW DIRECTIVE
        </Button>
      </div>

      {/* Strip of counts */}
      <div className="grid grid-cols-3 gap-2 font-data text-[11px]">
        <div className="flex items-center justify-between bg-[#0a0f1c] border border-[#1c2540] px-3 py-2">
          <span className="text-gray-500 uppercase tracking-[0.16em]">TOTAL</span>
          <span className="text-gray-100 tabular-nums">{rules.length}</span>
        </div>
        <div className="flex items-center justify-between bg-[#0a0f1c] border border-amber-700/50 px-3 py-2">
          <span className="text-amber-400 uppercase tracking-[0.16em]">ACTIVE</span>
          <span className="text-amber-300 tabular-nums">{activeCount}</span>
        </div>
        <div className="flex items-center justify-between bg-[#0a0f1c] border border-[#1c2540] px-3 py-2">
          <span className="text-gray-500 uppercase tracking-[0.16em]">DORMANT</span>
          <span className="text-gray-400 tabular-nums">
            {rules.length - activeCount}
          </span>
        </div>
      </div>

      {loading ? (
        <Panel label="LOADING" title="Fetching rulebook">
          <LoadingLines rows={4} />
        </Panel>
      ) : rules.length === 0 ? (
        <Panel label="EMPTY">
          <EmptyState
            icon={<Workflow className="w-5 h-5" />}
            title="NO DIRECTIVES PROGRAMMED"
            description="Automation rules let the system act on detections (greetings, GPIO triggers, notifications, shell hooks)."
            action={
              <Button
                variant="primary"
                size="md"
                iconLeft={<Plus className="w-4 h-4" />}
                onClick={() => setCreating(true)}
              >
                PROGRAM FIRST RULE
              </Button>
            }
          />
        </Panel>
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
