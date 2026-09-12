import { useState } from "react";

import { Sidebar } from "./components/Sidebar";
import { ProjectView } from "./components/ProjectView";

export default function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  return (
    <div className="flex h-full">
      <Sidebar selectedId={selectedId} onSelect={setSelectedId} />
      {selectedId ? (
        <ProjectView key={selectedId} projectId={selectedId} />
      ) : (
        <main className="flex flex-1 items-center justify-center">
          <div className="max-w-md text-center">
            <h2 className="text-lg font-semibold text-neutral-200">AI Workbench</h2>
            <p className="mt-2 text-sm text-neutral-500">
              Select a project, or create one. A project gets an isolated k3s
              workspace with an agent attached to it.
            </p>
          </div>
        </main>
      )}
    </div>
  );
}
