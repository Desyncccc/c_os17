package port;

import android.os.Binder;
import android.os.Parcel;
import android.os.ServiceManager;
import android.util.Log;

/**
 * Stand-ins for Oplus vendor AIDL services that a non-Oplus vendor image does not have.
 *
 * ColorOS looks some vendor services up on hot paths with a blocking getService(), which polls for five
 * seconds when the name is not registered: IOplusProject on the input thread (every power-key press was
 * delayed by 5 s on the OnePlus 7 Pro port) and IPerformance in mediaserver (once per codec). Registering
 * any binder under the name ends the wait. Each stand-in answers the interface-descriptor query with the
 * real descriptor and every method call with "no exception" followed by zeroed return values.
 *
 * Only use this for interfaces whose callers accept zero/empty results. Interfaces that return arrays or
 * structured data (for example IDisplayPanelFeature) must not be stubbed this way.
 *
 * Usage: CLASSPATH=oplus-port-stubs.dex app_process /system/bin port.OplusStubs <descriptor>/<instance>...
 */
public final class OplusStubs {
    private static final String TAG = "OplusStub";
    private static final int LAST_METHOD_CODE = 0x00ffffff;      // IBinder.LAST_CALL_TRANSACTION

    static final class StubBinder extends Binder {
        private final String descriptor;

        StubBinder(String descriptor) {
            super(descriptor);
            this.descriptor = descriptor;
        }

        @Override
        protected boolean onTransact(int code, Parcel data, Parcel reply, int flags) {
            if (code < 1 || code > LAST_METHOD_CODE) {
                try {
                    return super.onTransact(code, data, reply, flags);   // descriptor query, ping, dump, ...
                } catch (Exception e) {
                    return false;
                }
            }
            Log.d(TAG, descriptor + " code=" + code);
            if (reply != null) {
                reply.writeNoException();
                for (int i = 0; i < 16; i++) {
                    reply.writeInt(0);
                }
            }
            return true;
        }
    }

    public static void main(String[] args) {
        for (String name : args) {
            String descriptor = name.substring(0, name.indexOf('/'));
            ServiceManager.addService(name, new StubBinder(descriptor));
            Log.i(TAG, "registered " + name);
        }
        Binder.joinThreadPool();
    }

    private OplusStubs() {}
}
