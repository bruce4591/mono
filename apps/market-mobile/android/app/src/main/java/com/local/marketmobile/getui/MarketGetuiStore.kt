package com.local.marketmobile.getui

import android.content.Context

object MarketGetuiStore {
  private const val PREFS = "market_getui"
  private const val CID = "cid"
  private const val ONLINE = "online"

  fun saveClientId(context: Context, cid: String) {
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
      .edit()
      .putString(CID, cid)
      .apply()
  }

  fun getClientId(context: Context): String? =
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(CID, null)

  fun saveOnline(context: Context, online: Boolean) {
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
      .edit()
      .putBoolean(ONLINE, online)
      .apply()
  }

  fun isOnline(context: Context): Boolean =
    context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getBoolean(ONLINE, false)
}
