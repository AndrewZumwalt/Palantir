import { useState } from "react";
import type { AutomationRule, Person } from "./AutomationPage";
import { Button } from "../ui/Button";
import { Select, TextInput } from "../ui/Field";
import { Modal } from "../ui/Modal";

interface Props {
  rule: AutomationRule | null;
  persons: Person[];
  onSave: (rule: Partial<AutomationRule>) => void | Promise<void>;
  onCancel: () => void;
}

type TriggerType =
  | "person_enters"
  | "person_exits"
  | "schedule"
  | "voice_command";
type ActionType = "gpio" | "tts" | "notification" | "command";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function SubHeader({ step, title }: { step: string; title: string }) {
  return (
    <div className="pt-2 pb-1">
      <div className="font-data text-[10px] uppercase tracking-[0.22em] text-amber-500">
        // {step}
      </div>
      <div className="text-sm font-semibold text-gray-100 mt-0.5">{title}</div>
    </div>
  );
}

export default function RuleEditor({ rule, persons, onSave, onCancel }: Props) {
  const [name, setName] = useState(rule?.name || "");
  const [description, setDescription] = useState(rule?.description || "");
  const [triggerType, setTriggerType] = useState<TriggerType>(
    (rule?.trigger_type as TriggerType) || "person_enters"
  );
  const [actionType, setActionType] = useState<ActionType>(
    (rule?.action_type as ActionType) || "tts"
  );
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);

  const [personId, setPersonId] = useState(
    String(rule?.trigger_config?.person_id ?? "")
  );
  const [role, setRole] = useState(String(rule?.trigger_config?.role ?? ""));
  const [scheduleTime, setScheduleTime] = useState(
    String(rule?.trigger_config?.time ?? "08:00")
  );
  const [scheduleDays, setScheduleDays] = useState<string[]>(
    Array.isArray(rule?.trigger_config?.days)
      ? (rule?.trigger_config?.days as string[])
      : []
  );
  const [phrase, setPhrase] = useState(
    String(rule?.trigger_config?.phrase ?? "")
  );

  const [gpioPin, setGpioPin] = useState(
    String(rule?.action_config?.pin ?? "17")
  );
  const [gpioState, setGpioState] = useState(
    String(rule?.action_config?.state ?? "high")
  );
  const [gpioDuration, setGpioDuration] = useState(
    String(rule?.action_config?.duration_ms ?? "")
  );
  const [ttsText, setTtsText] = useState(
    String(rule?.action_config?.text ?? "")
  );
  const [notifMessage, setNotifMessage] = useState(
    String(rule?.action_config?.message ?? "")
  );
  const [shellCmd, setShellCmd] = useState(
    String(rule?.action_config?.shell ?? "")
  );

  const toggleDay = (day: string) => {
    const lower = day.toLowerCase();
    setScheduleDays((prev) =>
      prev.includes(lower) ? prev.filter((d) => d !== lower) : [...prev, lower]
    );
  };

  const buildTriggerConfig = () => {
    switch (triggerType) {
      case "person_enters":
      case "person_exits": {
        const cfg: Record<string, string> = {};
        if (personId) cfg.person_id = personId;
        if (role) cfg.role = role;
        return cfg;
      }
      case "schedule":
        return { time: scheduleTime, days: scheduleDays };
      case "voice_command":
        return { phrase };
    }
  };

  const buildActionConfig = () => {
    switch (actionType) {
      case "gpio": {
        const cfg: Record<string, unknown> = {
          pin: Number(gpioPin),
          state: gpioState,
        };
        if (gpioDuration) cfg.duration_ms = Number(gpioDuration);
        return cfg;
      }
      case "tts":
        return { text: ttsText };
      case "notification":
        return { message: notifMessage };
      case "command":
        return { shell: shellCmd };
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    await onSave({
      name: name.trim(),
      description: description.trim(),
      trigger_type: triggerType,
      trigger_config: buildTriggerConfig(),
      action_type: actionType,
      action_config: buildActionConfig(),
      enabled,
    });
  };

  return (
    <Modal
      open
      onClose={onCancel}
      label="DIRECTIVE EDITOR"
      title={rule ? "Edit directive" : "New directive"}
      size="lg"
      footer={
        <>
          <Button variant="ghost" onClick={onCancel}>
            CANCEL
          </Button>
          <Button
            variant="primary"
            onClick={handleSubmit}
            disabled={!name.trim()}
          >
            {rule ? "SAVE CHANGES" : "CREATE DIRECTIVE"}
          </Button>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="space-y-5">
        <SubHeader step="STEP 1" title="Identity" />
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <TextInput
            label="RULE NAME"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Greet the teacher"
          />
          <TextInput
            label="DESCRIPTION"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional note"
          />
        </div>

        <SubHeader step="STEP 2" title="Trigger — when it fires" />
        <Select
          label="TYPE"
          value={triggerType}
          onChange={(e) => setTriggerType(e.target.value as TriggerType)}
        >
          <option value="person_enters">When a subject enters</option>
          <option value="person_exits">When a subject exits</option>
          <option value="schedule">At scheduled time</option>
          <option value="voice_command">On voice command</option>
        </Select>

        {(triggerType === "person_enters" ||
          triggerType === "person_exits") && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Select
              label="SPECIFIC SUBJECT"
              value={personId}
              onChange={(e) => setPersonId(e.target.value)}
            >
              <option value="">Any subject</option>
              {persons.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
            <Select
              label="OR ANY ROLE"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            >
              <option value="">Any role</option>
              <option value="teacher">Teacher</option>
              <option value="student">Student</option>
              <option value="admin">Admin</option>
              <option value="guest">Guest</option>
            </Select>
          </div>
        )}

        {triggerType === "schedule" && (
          <div className="space-y-3">
            <TextInput
              label="TIME (HH:MM)"
              type="time"
              value={scheduleTime}
              onChange={(e) => setScheduleTime(e.target.value)}
            />
            <div>
              <div className="font-data text-[10px] uppercase tracking-[0.18em] text-gray-400 mb-1.5">
                // DAYS (leave all off = daily)
              </div>
              <div className="flex flex-wrap gap-1">
                {DAYS.map((d) => {
                  const active = scheduleDays.includes(d.toLowerCase());
                  return (
                    <button
                      key={d}
                      type="button"
                      onClick={() => toggleDay(d)}
                      className={[
                        "h-8 px-3 border font-data text-[11px] uppercase tracking-[0.14em]",
                        active
                          ? "bg-amber-500/15 border-amber-600/60 text-amber-200"
                          : "bg-[#05080f] border-[#1c2540] text-gray-400 hover:text-gray-200 hover:border-[#2a3658]",
                      ].join(" ")}
                    >
                      {d}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}

        {triggerType === "voice_command" && (
          <TextInput
            label="TRIGGER PHRASE (substring match)"
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            placeholder="e.g. lights on"
          />
        )}

        <SubHeader step="STEP 3" title="Action — what happens" />
        <Select
          label="TYPE"
          value={actionType}
          onChange={(e) => setActionType(e.target.value as ActionType)}
        >
          <option value="tts">Speak response</option>
          <option value="notification">Send notification</option>
          <option value="gpio">GPIO output</option>
          <option value="command">Run shell command</option>
        </Select>

        {actionType === "gpio" && (
          <div className="grid grid-cols-3 gap-3">
            <TextInput
              label="PIN"
              type="number"
              value={gpioPin}
              onChange={(e) => setGpioPin(e.target.value)}
            />
            <Select
              label="STATE"
              value={gpioState}
              onChange={(e) => setGpioState(e.target.value)}
            >
              <option value="high">High</option>
              <option value="low">Low</option>
              <option value="toggle">Toggle</option>
            </Select>
            <TextInput
              label="DURATION (ms, opt)"
              type="number"
              value={gpioDuration}
              onChange={(e) => setGpioDuration(e.target.value)}
              placeholder="—"
            />
          </div>
        )}

        {actionType === "tts" && (
          <TextInput
            label="TEXT TO SPEAK"
            value={ttsText}
            onChange={(e) => setTtsText(e.target.value)}
            placeholder="Welcome back."
          />
        )}

        {actionType === "notification" && (
          <TextInput
            label="MESSAGE"
            value={notifMessage}
            onChange={(e) => setNotifMessage(e.target.value)}
            placeholder="Alert details"
          />
        )}

        {actionType === "command" && (
          <TextInput
            label="SHELL COMMAND (requires allow_shell_commands)"
            value={shellCmd}
            onChange={(e) => setShellCmd(e.target.value)}
            placeholder="/usr/local/bin/my-script"
            className="font-data"
          />
        )}

        <div className="pt-2 border-t border-[#1c2540] flex items-center justify-between">
          <div>
            <div className="font-data text-[10px] uppercase tracking-[0.22em] text-amber-500">
              // ACTIVATION
            </div>
            <div className="text-xs text-gray-500 mt-0.5">
              Directive begins firing once saved.
            </div>
          </div>
          <label className="inline-flex items-center gap-2 cursor-pointer font-data text-[11px] uppercase tracking-[0.14em] text-gray-400">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
              className="sr-only peer"
            />
            <span className="w-10 h-5 bg-[#05080f] border border-[#2a3658] peer-checked:border-amber-500 peer-checked:bg-amber-500/20 relative transition">
              <span
                className={[
                  "absolute top-0.5 w-3 h-3 bg-gray-600 peer-checked:bg-amber-400 transition-all",
                  enabled ? "left-[calc(100%-0.875rem)]" : "left-0.5",
                ].join(" ")}
              />
            </span>
            <span className={enabled ? "text-amber-300" : "text-gray-500"}>
              {enabled ? "ENABLED" : "DISABLED"}
            </span>
          </label>
        </div>
      </form>
    </Modal>
  );
}
