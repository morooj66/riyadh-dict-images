import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import { DictionaryPage } from "./pages/DictionaryPage";
import "./App.css";

function LegacyRedirect() {
  const { id } = useParams<{ id?: string }>();
  return <Navigate to={id ? `/word/${id}` : "/"} replace />;
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/review" element={<Navigate to="/" replace />} />
        <Route path="/review/:id" element={<LegacyRedirect />} />
        <Route path="/selection/:id" element={<LegacyRedirect />} />
        <Route path="/words" element={<Navigate to="/" replace />} />
        <Route path="/entries/:id" element={<LegacyRedirect />} />
        <Route path="/*" element={<DictionaryPage />} />
      </Routes>
    </BrowserRouter>
  );
}
