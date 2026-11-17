// Integrations → Frigate: connection card + camera setup (moved here from Settings → Images, P144-s01).
// Landing spot for P144's Frigate blocks — each one a self-contained card dropped in with one line.
import { Card, CardContent } from "@/components/ui/card";
import Cameras from "../Cameras";
import EpisodesCard from "./EpisodesCard";
import FaceVotesCard from "./FaceVotesCard";
import FaceSensorsCard from "./FaceSensorsCard";
import QuestionsCard from "@/components/QuestionsCard";
import ModulesCard from "./ModulesCard";
import FrigateCard from "./FrigateCard";
import { FrigateModelCard } from "../settings/llm/FrigateModelCard";
import FrigateFacesCard from "./FrigateFacesCard";
import FrigateUserCard from "./FrigateUserCard";

export default function TabFrigate() {
  return (
    <div className="space-y-6">
      <EpisodesCard />
      <FrigateCard />
      <FrigateUserCard />
      <FaceVotesCard />
      <FaceSensorsCard />
      {/* J12 / R139: provisional — the final GUI session decides (GUI list G24) */}
      {/* J21 5e: the module panel (provisional placement, G19–G23) */}
      <ModulesCard />
      {/* J20: one card «Hvem / Hva / Hvor?» (admin: all three parts) */}
      <QuestionsCard admin />
      <FrigateModelCard />
      <FrigateModelCard role="frigate_conclude" />
      <FrigateFacesCard />

      {/* Cameras renders its own header (title + the global analysis switch on the right) */}
      <Card>
        <CardContent style={{ paddingTop: 20 }}>
          <Cameras />
        </CardContent>
      </Card>
    </div>
  );
}
