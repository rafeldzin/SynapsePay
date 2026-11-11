import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import MilestoneDetailPage from "./pages/MilestoneDetailPage";
import MilestoneListPage from "./pages/MilestoneListPage";
import NotBuiltPage from "./pages/NotBuiltPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<MilestoneListPage />} />
        <Route path="milestones/:id" element={<MilestoneDetailPage />} />
        {/* Layar 3–5 dibangun di tahap berikutnya. */}
        <Route path="ledger" element={<NotBuiltPage title="Ledger payout" stage={3} />} />
        <Route path="timeline" element={<NotBuiltPage title="Timeline event" stage={4} />} />
        <Route path="cases" element={<NotBuiltPage title="Kasus" stage={5} />} />
        <Route path="*" element={<NotBuiltPage title="Halaman tidak ditemukan" />} />
      </Route>
    </Routes>
  );
}
