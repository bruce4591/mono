package com.local.marketmobile.getui

import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import com.igexin.sdk.PushManager

class MarketGetuiModule(
  private val reactContext: ReactApplicationContext
) : ReactContextBaseJavaModule(reactContext) {
  override fun getName(): String = "MarketGetui"

  @ReactMethod
  fun initialize(promise: Promise) {
    try {
      val context = reactContext.applicationContext
      PushManager.getInstance().preInit(context)
      PushManager.getInstance().initialize(context, MarketGetuiPushService::class.java)
      PushManager.getInstance().registerPushIntentService(
        context,
        MarketGetuiIntentService::class.java
      )
      promise.resolve(true)
    } catch (error: Exception) {
      promise.reject("GETUI_INIT_FAILED", error)
    }
  }

  @ReactMethod
  fun getClientId(promise: Promise) {
    promise.resolve(MarketGetuiStore.getClientId(reactContext.applicationContext))
  }

  @ReactMethod
  fun isOnline(promise: Promise) {
    promise.resolve(MarketGetuiStore.isOnline(reactContext.applicationContext))
  }
}
