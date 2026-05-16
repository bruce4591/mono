export type BoardGroup = "tradfi" | "crypto";
export type HomeRoute = { name: "home"; group?: BoardGroup; boardKey?: string | null };

export type InstrumentRoute = {
  name: "instrument";
  market: string;
  symbol: string;
  period?: string;
  returnTo?: HomeRoute;
};

export type AlertEventsRoute = { name: "alertEvents" };
export type AlertRulesRoute = { name: "alertRules" };
export type StrategiesRoute = { name: "strategies" };
export type SettingsRoute = { name: "settings" };

export type AppRoute =
  | HomeRoute
  | InstrumentRoute
  | AlertEventsRoute
  | AlertRulesRoute
  | StrategiesRoute
  | SettingsRoute;

export const homeRoute: HomeRoute = { name: "home" };
