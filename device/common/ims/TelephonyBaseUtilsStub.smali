# cepheus port: the Mi 9 base ROM's IMS app is Xiaomi's build and asks this MIUI helper whether it runs on
# MIUI. The base ROM answers "no" from a boot-classpath stub jar (xiaomi-telephony-stub.jar); ColorOS has no
# such class, so the same answer is compiled into the app.
.class public Landroid/telephony/TelephonyBaseUtilsStub;
.super Ljava/lang/Object;
.source "TelephonyBaseUtilsStub.java"


# direct methods
.method public constructor <init>()V
    .locals 0

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method

.method public static isMiuiRom()Z
    .locals 1

    const/4 v0, 0x0

    return v0
.end method
