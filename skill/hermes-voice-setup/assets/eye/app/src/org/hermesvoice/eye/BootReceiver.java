package org.hermesvoice.eye;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Starts the camera service after the Echo boots. The camera itself stays closed until asked. */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            context.startService(new Intent(context, EyeService.class));
        }
    }
}
