import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import CasePage from "./pages/CasePage";
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
        <Route path="cases" element={<CasePage />} />
        <Route path="*" element={<NotBuiltPage title="Halaman tidak ditemukan" />} />
      </Route>
    </Routes>
  );
}
