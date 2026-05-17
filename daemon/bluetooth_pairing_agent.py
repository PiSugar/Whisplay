import threading


class BluetoothPairingAgent:
    AGENT_PATH = "/org/whisplay/Agent1"

    def __init__(self, state_callback):
        self._state_callback = state_callback
        self._lock = threading.RLock()
        self._decision_event = threading.Event()
        self._ready_event = threading.Event()
        self._decision = None
        self._loop = None
        self._thread = None
        self._bus = None
        self._bluez = None
        self._agent = None
        self._started = False

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
            self._ready_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()
        self._ready_event.wait(timeout=5.0)

    def stop(self):
        with self._lock:
            self._started = False
            if self._loop is not None:
                self._loop.quit()
            self._decision_event.set()
            self._ready_event.set()

    def confirm(self):
        self._decision = True
        self._decision_event.set()

    def cancel(self):
        self._decision = False
        self._decision_event.set()

    def _run_loop(self):
        import dbus
        import dbus.exceptions
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        outer = self
        DBusGMainLoop(set_as_default=True)
        bus = dbus.SystemBus()
        self._bus = bus
        self._bluez = dbus.Interface(
            bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager",
        )

        class Agent(dbus.service.Object):
            def __init__(self, conn, path):
                super().__init__(conn, path)

            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Release(self):
                outer._state_callback({"active": False})

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="s")
            def RequestPinCode(self, _device):
                raise dbus.exceptions.DBusException("org.bluez.Error.Rejected", "PIN code entry not supported")

            @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
            def DisplayPinCode(self, _device, pincode):
                outer._state_callback(
                    {
                        "active": True,
                        "message": "Type this PIN on keyboard",
                        "detail": str(pincode),
                        "requires_confirmation": False,
                    }
                )

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="u")
            def RequestPasskey(self, _device):
                raise dbus.exceptions.DBusException("org.bluez.Error.Rejected", "Passkey entry not supported")

            @dbus.service.method("org.bluez.Agent1", in_signature="ouq", out_signature="")
            def DisplayPasskey(self, _device, passkey, entered):
                outer._state_callback(
                    {
                        "active": True,
                        "message": "Type code on keyboard",
                        "detail": f"{int(passkey):06d}  {int(entered)}/6",
                        "requires_confirmation": False,
                    }
                )

            @dbus.service.method("org.bluez.Agent1", in_signature="ou", out_signature="")
            def RequestConfirmation(self, _device, passkey):
                outer._decision = None
                outer._decision_event.clear()
                outer._state_callback(
                    {
                        "active": True,
                        "message": "Confirm pairing code",
                        "detail": f"{int(passkey):06d}",
                        "requires_confirmation": True,
                    }
                )
                outer._decision_event.wait(timeout=30.0)
                decision = outer._decision
                outer._state_callback({"active": False})
                if decision is True:
                    return
                raise dbus.exceptions.DBusException("org.bluez.Error.Rejected", "Pairing rejected")

            @dbus.service.method("org.bluez.Agent1", in_signature="o", out_signature="")
            def RequestAuthorization(self, _device):
                return

            @dbus.service.method("org.bluez.Agent1", in_signature="os", out_signature="")
            def AuthorizeService(self, _device, _uuid):
                return

            @dbus.service.method("org.bluez.Agent1", in_signature="", out_signature="")
            def Cancel(self):
                outer._state_callback({"active": False})

        self._agent = Agent(bus, self.AGENT_PATH)
        agent_manager = dbus.Interface(
            bus.get_object("org.bluez", "/org/bluez"),
            "org.bluez.AgentManager1",
        )
        try:
            agent_manager.RegisterAgent(self.AGENT_PATH, "KeyboardDisplay")
        except Exception:
            pass
        try:
            agent_manager.RequestDefaultAgent(self.AGENT_PATH)
        except Exception:
            pass
        self._ready_event.set()

        self._loop = GLib.MainLoop()
        self._loop.run()


