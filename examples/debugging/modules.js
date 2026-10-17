send({pid: Process.id, modules: Process.enumerateModules().map(m => ({name: m.name, base: m.base.toString(), size: m.size}))});
