package android.os;

/** Compile-time stand-in for the platform class (only the members OplusStubs uses). Not packaged. */
public class Binder implements IBinder {
    public Binder(String descriptor) {}
    protected boolean onTransact(int code, Parcel data, Parcel reply, int flags) throws RemoteException { return false; }
    public static void joinThreadPool() {}
}
