import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import DashboardPage from "./components/Dashboard/DashboardPage";

function PlaceholderPage({ title }: { title: string }) {
  return (
    <div>
      <h1 className="text-2xl font-bold tracking-tight mb-2">{title}</h1>
      <p className="text-gray-500">Coming soon in the next phase.</p>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route
          path="attendance"
          element={<PlaceholderPage title="Attendance" />}
        />
        <Route
          path="enrollment"
          element={<PlaceholderPage title="Enrollment" />}
        />
        <Route
          path="events"
          element={<PlaceholderPage title="Event Log" />}
        />
        <Route
          path="settings"
          element={<PlaceholderPage title="Settings" />}
        />
      </Route>
    </Routes>
  );
}
