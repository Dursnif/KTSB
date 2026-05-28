import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import axios from "axios";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Loader2 } from "lucide-react";
import { apiGetKareSettings, apiPutKareSettings, type ContributorMode, type PersonalityMode } from "@/services/api";
import { BASE, token } from "./constants";
import { useSaveState, FieldRow, SaveFeedback, RadioOption } from "./shared";

type UserEntry = { username: string; display_name: string; role: string };

export default function TabKareInnstillinger() {
  const { t } = useTranslation();
  const [assistantName, setAssistantName] = useState("Kåre");
  const [hotword, setHotword] = useState("Kåre");
  const [personalityMode, setPersonalityMode] = useState<PersonalityMode>("standard");
  const [customText, setCustomText] = useState("");
  const [defaultPersonality, setDefaultPersonality] = useState("");
  const [mode, setMode] = useState<ContributorMode>("selected");
  const [allowedUsers, setAllowedUsers] = useState<string[]>([]);
  const [allUsers, setAllUsers] = useState<UserEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const ssPersonality = useSaveState();
  const ssSelfimage   = useSaveState();

  useEffect(() => {
    Promise.all([
      apiGetKareSettings(),
      axios.get(`${BASE}/api/users`, { headers: { Authorization: `Bearer ${token()}` } }),
    ]).then(([kare, usersRes]) => {
      setAssistantName(kare.assistant_name ?? "Kåre");
      setHotword(kare.hotword ?? "Kåre");
      setPersonalityMode(kare.personality_mode ?? "standard");
      setCustomText(kare.personality_core_custom ?? "");
      setDefaultPersonality(kare.personality_core_default ?? "");
      setMode(kare.personality_self.contributors);
      setAllowedUsers(kare.personality_self.allowed_users);
      setAllUsers((usersRes.data as UserEntry[]).filter(u => u.username !== "admin"));
    }).catch(() => {}).finally(() => setLoading(false));
  }, []);

  const toggleUser = (username: string) => {
    setAllowedUsers(prev =>
      prev.includes(username) ? prev.filter(u => u !== username) : [...prev, username]
    );
  };

  const savePersonality = async () => {
    ssPersonality.saving();
    try {
      await apiPutKareSettings({
        personality_mode: personalityMode,
        personality_core_custom: personalityMode === "egendefinert" ? customText : undefined,
      });
      ssPersonality.saved();
    } catch { ssPersonality.error(); }
  };

  const saveSelfimage = async () => {
    ssSelfimage.saving();
    try {
      await apiPutKareSettings({
        assistant_name: assistantName,
        hotword,
        personality_self: { contributors: mode, allowed_users: allowedUsers },
      });
      ssSelfimage.saved();
    } catch { ssSelfimage.error(); }
  };

  if (loading) return <div className="text-muted-foreground text-sm">{t("common.loading")}</div>;

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings.kare.personality_mode.title")}</CardTitle>
          <CardDescription>{t("settings.kare.personality_mode.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border mb-4">
            <RadioOption
              value="minimal"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.minimal_label")}
              description={t("settings.kare.personality_mode.minimal_desc")}
            />
            <RadioOption
              value="letvekt"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.letvekt_label")}
              description={t("settings.kare.personality_mode.letvekt_desc")}
            />
            <RadioOption
              value="standard"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.standard_label")}
              description={t("settings.kare.personality_mode.standard_desc")}
            />
            <RadioOption
              value="full"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.full_label")}
              description={t("settings.kare.personality_mode.full_desc")}
            />
            <RadioOption
              value="komplett"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.komplett_label")}
              description={t("settings.kare.personality_mode.komplett_desc")}
            />
            <RadioOption
              value="egendefinert"
              current={personalityMode}
              onChange={(v) => setPersonalityMode(v as PersonalityMode)}
              label={t("settings.kare.personality_mode.egendefinert_label")}
              description={t("settings.kare.personality_mode.egendefinert_desc")}
            />
          </div>
          {personalityMode === "egendefinert" && (
            <div className="mt-2 space-y-2">
              {customText === "" && defaultPersonality && (
                <div className="flex justify-end">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setCustomText(defaultPersonality)}
                  >
                    {t("settings.kare.personality_mode.load_default_btn")}
                  </Button>
                </div>
              )}
              <textarea
                value={customText}
                onChange={(e) => setCustomText(e.target.value)}
                placeholder={t("settings.kare.personality_mode.custom_placeholder")}
                rows={16}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono resize-y focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <p className="text-xs text-muted-foreground">{t("settings.kare.personality_mode.custom_hint")}</p>
            </div>
          )}
          <p className="text-xs text-muted-foreground mt-3">{t("settings.kare.personality_mode.hot_reload_hint")}</p>
          <div className="flex items-center gap-3 mt-4">
            <Button onClick={savePersonality} disabled={ssPersonality.state === "saving"}>
              {ssPersonality.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssPersonality.state} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("settings.kare.selfimage.title")}</CardTitle>
          <CardDescription>{t("settings.kare.selfimage.description")}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border mb-6">
            <FieldRow label={t("settings.kare.selfimage.name_label")} hint={t("settings.kare.selfimage.name_hint")}>
              <Input value={assistantName} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setAssistantName(e.target.value)} placeholder="Kåre" className="w-48" />
            </FieldRow>
            <FieldRow label={t("settings.kare.selfimage.hotword_label")} hint={t("settings.kare.selfimage.hotword_hint")}>
              <Input value={hotword} onChange={(e: React.ChangeEvent<HTMLInputElement>) => setHotword(e.target.value)} placeholder="Kåre" className="w-48" />
            </FieldRow>
          </div>
          <div className="mb-2">
            <Label className="text-sm font-medium">{t("settings.kare.selfimage.who_shapes")}</Label>
          </div>
          <div className="divide-y divide-border mb-5">
            <RadioOption
              value="all"
              current={mode}
              onChange={(v) => setMode(v as ContributorMode)}
              label={t("settings.kare.selfimage.modes.all_label")}
              description={t("settings.kare.selfimage.modes.all_desc")}
            />
            <RadioOption
              value="selected"
              current={mode}
              onChange={(v) => setMode(v as ContributorMode)}
              label={t("settings.kare.selfimage.modes.selected_label")}
              description={t("settings.kare.selfimage.modes.selected_desc")}
            />
            <RadioOption
              value="admin_only"
              current={mode}
              onChange={(v) => setMode(v as ContributorMode)}
              label={t("settings.kare.selfimage.modes.admin_only_label")}
              description={t("settings.kare.selfimage.modes.admin_only_desc")}
            />
          </div>

          {mode === "selected" && (
            <div className="mb-5">
              <Label className="text-sm font-medium mb-2 block">{t("settings.kare.selfimage.allowed_users_label")}</Label>
              {allUsers.length === 0 && (
                <p className="text-xs text-muted-foreground">{t("settings.kare.selfimage.no_users")}</p>
              )}
              <div className="flex flex-wrap gap-2 mt-1">
                {allUsers.map(u => {
                  const checked = allowedUsers.includes(u.username);
                  return (
                    <button
                      key={u.username}
                      type="button"
                      onClick={() => toggleUser(u.username)}
                      className={`flex items-center gap-2 px-3 py-1.5 rounded-full border text-sm transition-colors ${
                        checked
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:border-muted-foreground"
                      }`}
                    >
                      <span className={`w-3.5 h-3.5 rounded flex-shrink-0 border flex items-center justify-center transition-colors ${checked ? "bg-primary border-primary" : "border-muted-foreground"}`}>
                        {checked && (
                          <svg viewBox="0 0 12 12" className="w-2.5 h-2.5 fill-white">
                            <path d="M2 6l3 3 5-5" stroke="white" strokeWidth="1.5" fill="none" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        )}
                      </span>
                      {u.display_name || u.username}
                    </button>
                  );
                })}
              </div>
              <p className="text-xs text-muted-foreground mt-2">{t("settings.kare.selfimage.admin_note")}</p>
            </div>
          )}

          <div className="flex items-center gap-3">
            <Button onClick={saveSelfimage} disabled={ssSelfimage.state === "saving"}>
              {ssSelfimage.state === "saving" && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {t("common.save")}
            </Button>
            <SaveFeedback state={ssSelfimage.state} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
