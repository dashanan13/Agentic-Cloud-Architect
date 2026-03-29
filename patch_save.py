import re

with open("App_Backend/settings_server.py", "r") as f:
    text = f.read()

pattern = """        app_settings = load_app_settings()
        bootstrap_result = bootstrap_default_foundry_resources(app_settings)
        if bootstrap_result.get("settingsUpdated"):
            write_app_settings_file(app_settings)

        thread_entry = {
            "id": body.project.id,
            "name": body.project.name,
            "cloud": body.project.cloud,
            "projectDir": project_dir,
            "metadataPath": metadata_path,
        }

        seeded_metadata = dict(existing_metadata)
        incoming_chat_thread_id = str(
            body.project.foundryChatThreadId
            or body.project.foundryThreadId
            or ""
        ).strip()
        if incoming_chat_thread_id:
            seeded_metadata["foundryChatThreadId"] = incoming_chat_thread_id
            seeded_metadata["foundryThreadId"] = incoming_chat_thread_id

        incoming_validation_thread_idimport re

with open("App_Baal
with ophre    text = f.read()

pattern = """        app_settingre
pattern = """     se        bootstrap_result = bootstrap_default_foundry_rva        if bootstrap_result.get("settingsUpdated"):
            write_app_sad            write_app_settings_file(app_settings)
se
        thread_entry = {
            "id": bodypro            "id": body.et            "name": body.project.d_            "cloud": body.project.clo              "projectDir": project_dir,
_t           ["settings"]
        seeded_        }

        seeded_metadata = dict  
       und        incoming_chat_thread_id = str(
         lt            body.project.foundryChatTat            or body.project.foundryThreadId)             or ""
        ).strip()
       e        ).strip(un        if state(
            seeded_metadata["found              seeded_metadata["foundryThreadId"] = incoming_chat_thread_id

 ng
        incoming_validation_thread_idimport re

with oa,
            pers
with open("App_Baal
with ophre    text = f.r = with ophre    textst
pattern = """        app_sedrypattern = """     se        bootsat            write_app_sad            write_app_settings_file(app_settings)
se
        thread_entry = {
            "id": bodypipse
        thread_entry = {
            "id": bodypro            "id": bo)
               "id": bodyp {_t           ["settings"]
        seeded_        }

        seeded_metadata = dict  
       und        incoming_chat_thread_id = str(
         lt            bodyt         seeded_        } N
        seeded_metadatfou       und        incoming_chatct         lt            body.project.foundryChatea        ).strip()
       e        ).strip(un        if state(
            seeded_metadata["found   _project_found       e        je            seeded_metadata["found        t"
 ng
        incoming_validation_thread_idimport re

with oa,
            pers
with open("App_Baal
with ophtio  hr
adId or "").strip()
        if not foundry_vali      _thwith open("App_  with ophre    texttipattern = """        app_sedrypattern = """   thse
        thread_entry = {
            "id": bodypipse
        thread_entry = {
            "id": bodypro            "id": bo)
           " hr            "id": body_id        thread_entry =dation            "id": bodyppe               "id": bodyp {_t           ["sead        seeded_        }

        seeded_metadata = t)
        seeded_metadatd/s       und        incoming_chat           lt            bodyt         seeded_   (n        seeded_metadatfou       und        incomit("WARN       e        ).strip happen!")
