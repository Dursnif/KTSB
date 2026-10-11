import { createContext, useContext } from "react";

export const AssistantNameContext = createContext<string>("Kåre");

export function useAssistantName() {
  return useContext(AssistantNameContext);
}
