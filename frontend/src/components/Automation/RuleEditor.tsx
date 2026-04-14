import { useState } from "react";
import type { AutomationRule, Person } from "./AutomationPage";

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

function Label({ children }: { children: React.ReactNode }) {
  return (
    <label className="block text-xs font-medium text-gray-700 mb-1">
      {children}
    </label>
  );
}

function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={
        "w-full px-3 py-1.5 text-sm border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-indigo-500 " +
        (props.className || "")
      }
    />
  );
}

function Select({
  children,
  ...props
}: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={
        "w-full px-3 py-1.5 text-sm border border-gray-200 rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 " +
        (props.className || "")
      }
    >
      {children}
    </select>
  );
}

export default function RuleEditor({ rule, persons, onSave, onCancel }: Props) {
  const [name, setName] = useState(rule?.name || "");
  const [description, setDescription] = useState(rule?.description || "");
  const [triggerType, setTriggerType] = useState<TriggerType>(
    (rule?.trigger_type as TriggerType) || "person_enters",
  );
  const [actionType, setActionType] = useState<ActionType>(
    (rule?.action_type as ActionType) || "tts",
  );
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);

  // Trigger config fields
  const [personId, setPersonId] = useState(
    String(rule?.trigger_config?.person_id ?? ""),
  );
  const [role, setRole] = useState(
    String(rule?.trigger_config?.role ?? ""),
  );
  const [scheduleTime, setScheduleTime] = useState(
    String(rule?.trigger_config?.time ?? "08:00"),
  );
  const [scheduleDays, setScheduleDays] = useState<string[]>(
    Array.isArray(rule?.trigger_config?.days)
      ? (rule?.trigger_config?.days as string[])
      : [],
  );
  const [phrase, setPhrase] = useState(
    String(rule?.trigger_config?.phrase ?? ""),
  );

  // Action config fields
  const [gpioPin, setGpioPin] = useState(
    String(rule?.action_config?.pin ?? "17"),
  );
  const [gpioState, setGpioState] = useState(
    String(rule?.action_config?.state ?? "high"),
  );
  const [gpioDuration, setGpioDuration] = useState(
    String(rule?.action_config?.duration_ms ?? ""),
  );
  const [ttsText, setTtsText] = useState(
    String(rule?.action_config?.text ?? ""),
  );
  const [notifMessage, setNotifMessage] = useState(
    String(rule?.action_config?.message ?? ""),
  );
  const [shellCmd, setShellCmd] = useState(
    String(rule?.action_config?.shell ?? ""),
  );

  const toggleDay = (day: string) => {
    const lower = day.toLowerCase();
    setScheduleDays((prev) =>
      prev.includes(lower) ? prev.filter((d) => d !== lower) : [...prev, lower],
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-xl max-h-[90vh] overflow-y-auto">
        <form onSubmit={handleSubmit}>
          <div className="px-6 py-4 border-b border-gray-100">
            <h2 className="text-lg font-semibold">
              {rule ? "Edit Rule" : "New Automation Rule"}
            </h2>
          </div>

          <div className="p-6 space-y-4">
            <div>
              <Label>Rule name</Label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Turn on lights when teacher arrives"
                required
              />
            </div>

            <div>
              <Label>Description (optional)</Label>
              <Input
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What this rule does"
              />
            </div>

            {/* Trigger */}
            <div className="pt-3 border-t border-gray-100">
              <h3 className="text-sm font-semibold mb-2">Trigger</h3>
              <Label>Type</Label>
              <Select
                value={triggerType}
                onChange={(e) => setTriggerType(e.target.value as TriggerType)}
              >
                <option value="person_enters">When a person enters</option>
                <option value="person_exits">When a person exits</option>
                <option value="schedule">At scheduled time</option>
                <option value="voice_command">On voice command</option>
              </Select>

              {(triggerType === "person_enters" ||
                triggerType === "person_exits") && (
                <div className="grid grid-cols-2 gap-3 mt-3">
                  <div>
                    <Label>Specific person</Label>
                    <Select
                      value={personId}
                      onChange={(e) => setPersonId(e.target.value)}
                    >
                      <option value="">Any person</option>
                      {persons.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </Select>
                  </div>
                  <div>
                    <Label>Or any role</Label>
                    <Select value={role} onChange={(e) => setRole(e.target.value)}>
                      <option value="">Any role</option>
                      <option value="teacher">Teacher</option>
                      <option value="student">Student</option>
                      <option value="admin">Admin</option>
                      <option value="guest">Guest</option>
                    </Select>
                  </div>
                </div>
              )}

              {triggerType === "schedule" && (
                <div className="mt-3 space-y-3">
                  <div>
                    <Label>Time (HH:MM)</Label>
                    <Input
                      type="time"
                      value={scheduleTime}
                      onChange={(e) => setScheduleTime(e.target.value)}
                    />
                  </div>
                  <div>
                    <Label>Days (leave all off for daily)</Label>
                    <div className="flex gap-1 flex-wrap">
                      {DAYS.map((d) => {
                        const active = scheduleDays.includes(d.toLowerCase());
                        return (
                          <button
                            key={d}
                            type="button"
                            onClick={() => toggleDay(d)}
                            className={`px-2 py-1 rounded text-xs ${
                              active
                                ? "bg-indigo-600 text-white"
                                : "bg-gray-100 text-gray-700"
                            }`}
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
                <div className="mt-3">
                  <Label>Trigger phrase (substring match)</Label>
                  <Input
                    value={phrase}
                    onChange={(e) => setPhrase(e.target.value)}
                    placeholder="e.g. lights on"
                  />
                </div>
              )}
            </div>

            {/* Action */}
            <div className="pt-3 border-t border-gray-100">
              <h3 className="text-sm font-semibold mb-2">Action</h3>
              <Label>Type</Label>
              <Select
                value={actionType}
                onChange={(e) => setActionType(e.target.value as ActionType)}
              >
                <option value="tts">Speak response</option>
                <option value="notification">Send notification</option>
                <option value="gpio">GPIO output</option>
                <option value="command">Run shell command</option>
              </Select>

              {actionType === "gpio" && (
                <div className="grid grid-cols-3 gap-3 mt-3">
                  <div>
                    <Label>Pin</Label>
                    <Input
                      type="number"
                      value={gpioPin}
                      onChange={(e) => setGpioPin(e.target.value)}
                    />
                  </div>
                  <div>
                    <Label>State</Label>
                    <Select
                      value={gpioState}
                      onChange={(e) => setGpioState(e.target.value)}
                    >
                      <option value="high">High</option>
                      <option value="low">Low</option>
                      <option value="toggle">Toggle</option>
                    </Select>
                  </div>
                  <div>
                    <Label>Duration (ms, opt)</Label>
                    <Input
                      type="number"
                      value={gpioDuration}
                      onChange={(e) => setGpioDuration(e.target.value)}
                      placeholder="—"
                    />
                  </div>
                </div>
              )}

              {actionType === "tts" && (
                <div className="mt-3">
                  <Label>Text to speak</Label>
                  <Input
                    value={ttsText}
                    onChange={(e) => setTtsText(e.target.value)}
                    placeholder="Welcome back!"
                  />
                </div>
              )}

              {actionType === "notification" && (
                <div className="mt-3">
                  <Label>Notification message</Label>
                  <Input
                    value={notifMessage}
                    onChange={(e) => setNotifMessage(e.target.value)}
                    placeholder="Alert details"
                  />
                </div>
              )}

              {actionType === "command" && (
                <div className="mt-3">
                  <Label>Shell command (requires allow_shell_commands)</Label>
                  <Input
                    value={shellCmd}
                    onChange={(e) => setShellCmd(e.target.value)}
                    placeholder="/usr/local/bin/my-script"
                    className="font-mono"
                  />
                </div>
              )}
            </div>

            <div className="pt-3 border-t border-gray-100">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                  className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500"
                />
                Enable this rule immediately
              </label>
            </div>
          </div>

          <div className="px-6 py-4 border-t border-gray-100 bg-gray-50 flex justify-end gap-2">
            <button
              type="button"
              onClick={onCancel}
              className="px-4 py-2 text-sm rounded-md border border-gray-200 bg-white hover:bg-gray-100"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 text-sm rounded-md bg-indigo-600 text-white hover:bg-indigo-700"
            >
              {rule ? "Save Changes" : "Create Rule"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
