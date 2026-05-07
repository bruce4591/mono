export type HomeRoute = { name: "home" };
export type MenuRoute = { name: "menu" };

export type InstrumentRoute = {
  name: "instrument";
  market: string;
  symbol: string;
};

export type AlertEventsRoute = { name: "alertEvents" };
export type AlertRulesRoute = { name: "alertRules" };
export type SettingsRoute = { name: "settings" };

export type AppRoute =
  | MenuRoute
  | HomeRoute
  | InstrumentRoute
  | AlertEventsRoute
  | AlertRulesRoute
  | SettingsRoute;

export const homeRoute: HomeRoute = { name: "home" };
export const menuRoute: MenuRoute = { name: "menu" };
