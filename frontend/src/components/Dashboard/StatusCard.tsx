interface StatusCardProps {
  title: string;
  value: string | number;
  subtitle?: string;
  status?: "ok" | "warning" | "error";
}

export default function StatusCard({
  title,
  value,
  subtitle,
  status = "ok",
}: StatusCardProps) {
  const statusColors = {
    ok: "bg-green-50 border-green-200",
    warning: "bg-amber-50 border-amber-200",
    error: "bg-red-50 border-red-200",
  };

  const dotColors = {
    ok: "bg-green-500",
    warning: "bg-amber-500",
    error: "bg-red-500",
  };

  return (
    <div
      className={`rounded-xl border p-5 ${statusColors[status]} transition-all`}
    >
      <div className="flex items-center gap-2 mb-2">
        <div className={`w-2 h-2 rounded-full ${dotColors[status]}`} />
        <h3 className="text-sm font-medium text-gray-600">{title}</h3>
      </div>
      <p className="text-3xl font-semibold tracking-tight">{value}</p>
      {subtitle && (
        <p className="text-sm text-gray-500 mt-1">{subtitle}</p>
      )}
    </div>
  );
}
