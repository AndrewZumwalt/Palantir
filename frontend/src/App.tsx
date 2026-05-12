import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import DashboardPage from "./components/Dashboard/DashboardPage";
import AttendancePanel from "./components/Dashboard/AttendancePanel";
import CameraPage from "./components/Camera/CameraPage";
import ChatPage from "./components/Chat/ChatPage";
import EnrollmentWizard from "./components/Enrollment/EnrollmentWizard";
import EngagementPage from "./components/Engagement/EngagementPage";
import EventLogPage from "./components/EventLog/EventLogPage";
import SettingsPage from "./components/Settings/SettingsPage";
import SystemPage from "./components/System/SystemPage";
import AutomationPage from "./components/Automation/AutomationPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<DashboardPage />} />
        <Route path="camera" element={<CameraPage />} />
        <Route path="chat" element={<ChatPage />} />
        <Route path="attendance" element={<AttendancePanel />} />
        <Route path="engagement" element={<EngagementPage />} />
        <Route path="enrollment" element={<EnrollmentWizard />} />
        <Route path="automation" element={<AutomationPage />} />
        <Route path="events" element={<EventLogPage />} />
        <Route path="system" element={<SystemPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
    </Routes>
  );
}
