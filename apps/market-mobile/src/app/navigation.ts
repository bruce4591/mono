export type HomeRoute = { name: "home" };

export type InstrumentRoute = {
  name: "instrument";
  market: string;
  symbol: string;
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
