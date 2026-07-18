import { createContext, useContext, useEffect, useState } from "react";

const AssistantNameContext = createContext<string>("Kåre");

export function AssistantNameProvider({ children }: { children: React.ReactNode }) {
  const [name, setName] = useState("Kåre");
  useEffect(() => {
    fetch("/api/settings/assistant_name")
      .then(r => r.json())
      .then(d => { if (d.assistant_name) setName(d.assistant_name); })
      .catch(() => {});
  }, []);
  return (
    <AssistantNameContext.Provider value={name}>
      {children}
    </AssistantNameContext.Provider>
  );
}

export function useAssistantName() {
  return useContext(AssistantNameContext);
}
