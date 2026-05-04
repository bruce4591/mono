package com.local.marketmobile.getui

import android.content.Context
import android.util.Log
import com.igexin.sdk.GTIntentService
import com.igexin.sdk.message.GTCmdMessage
import com.igexin.sdk.message.GTNotificationMessage
import com.igexin.sdk.message.GTTransmitMessage

class MarketGetuiIntentService : GTIntentService() {
  override fun onReceiveServicePid(context: Context, pid: Int) {
    Log.d(TAG, "Getui service pid: $pid")
  }

  override fun onReceiveClientId(context: Context, clientid: String) {
    Log.i(TAG, "Getui CID received")
    MarketGetuiStore.saveClientId(context.applicationContext, clientid)
  }

  override fun onReceiveOnlineState(context: Context, online: Boolean) {
    MarketGetuiStore.saveOnline(context.applicationContext, online)
  }

  override fun onReceiveMessageData(context: Context, msg: GTTransmitMessage) {
    val payload = msg.payload?.toString(Charsets.UTF_8)
    Log.d(TAG, "Getui transmit message: ${payload ?: "<empty>"}")
  }

  override fun onNotificationMessageArrived(context: Context, message: GTNotificationMessage) {
    Log.d(TAG, "Getui notification arrived: ${message.title}")
  }

  override fun onNotificationMessageClicked(context: Context, message: GTNotificationMessage) {
    Log.d(TAG, "Getui notification clicked: ${message.title}")
  }

  override fun onReceiveCommandResult(context: Context, cmdMessage: GTCmdMessage) {
    Log.d(TAG, "Getui command result: $cmdMessage")
  }

  companion object {
    private const val TAG = "MarketGetui"
  }
}
