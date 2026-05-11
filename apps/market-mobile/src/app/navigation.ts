export type BoardGroup = "tradfi" | "crypto";
export type HomeRoute = { name: "home"; group?: BoardGroup };

export type InstrumentRoute = {
  name: "instrument";
  market: string;
  symbol: string;
  period?: string;
};

export type AlertEventsRoute = { name: "alertEvents" };
export type AlertRulesRoute = { name: "alertRules" };
export type SettingsRoute = { name: "settings" };

export type AppRoute =
  | HomeRoute
  | InstrumentRoute
  | AlertEventsRoute
  | AlertRulesRoute
  | SettingsRoute;

export const homeRoute: HomeRoute = { name: "home" };
