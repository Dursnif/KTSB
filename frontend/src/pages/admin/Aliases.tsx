// Rom og aliaser (N28 → P137): two tabs like the Settings page — Rom (the place register) and Aliaser (names).
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Loader2 } from "lucide-react";
import { apiGetAliases, apiGetRooms, apiPutAliases, apiPutRooms, type RoomsRaw, type RoomsView } from "@/services/api";
import TabRooms from "./rooms/TabRooms";
import TabAliases from "./rooms/TabAliases";

export default function Aliases() {
  const { t } = useTranslation();
  const [tab, setTab] = useState("rom");
  const [aliases, setAliases] = useState<Record<string, string> | null>(null);
  const [view, setView] = useState<RoomsView | null>(null);
  const [error, setError] = useState("");
  // Remount keys: the Aliaser tab starts fresh from loaded aliases; the Rom tab after the Aliaser tab saved rooms.
  const [aliasesRev, setAliasesRev] = useState(0);
  const [roomsRev, setRoomsRev] = useState(0);

  useEffect(() => {
    Promise.all([apiGetAliases(), apiGetRooms()])
      .then(([a, v]) => { setAliases(a.aliases ?? {}); setView(v); setAliasesRev(n => n + 1); })
      .catch(() => setError(t("common.error")));
  }, [t]);

  const saveAliases = async (next: Record<string, string>, rooms: RoomsRaw | null) => {
    try {
      if (rooms) {
        setView(await apiPutRooms(rooms));
        setRoomsRev(n => n + 1);
      }
      await apiPutAliases({ aliases: next });
      setAliases(next);
      // Names and places of new alias targets come from the server.
      setView(await apiGetRooms());
      return true;
    } catch {
      return false;
    }
  };

  const dot = (color: string) => (
    <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, display: "inline-block", marginRight: 5, flexShrink: 0 }} />
  );

  return (
    <div>
      <h1>{t("aliases.title")}</h1>
      <p style={{ color: "#999", fontSize: 13, marginBottom: 24, marginTop: -16 }}>{t("aliases.description")}</p>
      {!view || !aliases ? (
        error ? <p style={{ color: "#ff6b6b" }}>{error}</p> : <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      ) : (
        <Tabs value={tab} onValueChange={v => { if (v) setTab(v); }} className="w-full">
          <div className="w-full overflow-x-auto mb-6 pb-1">
            <TabsList className="flex-nowrap h-auto gap-1">
              <TabsTrigger value="rom" className="gap-1 whitespace-nowrap">{dot("#4db67a")}{t("rooms.tab")} ({view.places.filter(p => !p.hidden).length})</TabsTrigger>
              <TabsTrigger value="aliaser" className="gap-1 whitespace-nowrap">{dot("#7c6aff")}{t("aliases.tab_aliases")} ({Object.keys(aliases).length})</TabsTrigger>
            </TabsList>
          </div>
          {/* keepMounted: unsaved edits in one tab survive a visit to the other */}
          <TabsContent value="rom" keepMounted>
            <TabRooms key={`rooms-${roomsRev}`} view={view} onView={setView} />
          </TabsContent>
          <TabsContent value="aliaser" keepMounted>
            <TabAliases key={`aliases-${aliasesRev}`} data={aliases} view={view} onSave={saveAliases} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}
