import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import LedgerPage from "./pages/LedgerPage";
import MilestoneDetailPage from "./pages/MilestoneDetailPage";
import MilestoneListPage from "./pages/MilestoneListPage";
import NotBuiltPage from "./pages/NotBuiltPage";
import TimelinePage from "./pages/TimelinePage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<MilestoneListPage />} />
        <Route path="milestones/:id" element={<MilestoneDetailPage />} />
        <Route path="ledger" element={<LedgerPage />} />
        <Route path="timeline" element={<TimelinePage />} />
        {/* Layar 5 dibangun di tahap berikutnya. */}
        <Route path="cases" element={<NotBuiltPage title="Kasus" stage={5} />} />
        <Route path="*" element={<NotBuiltPage title="Halaman tidak ditemukan" />} />
      </Route>
    </Routes>
  );
}
