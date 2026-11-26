from backend.services.studio import jobs, intelligence
from backend.tests.test_studio import client, root, source


class Immediate:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

def test_import_persists_viral_template_and_brand_label(client, source, monkeypatch):
    monkeypatch.setattr(jobs, 'executor', Immediate())
    monkeypatch.setattr(intelligence, 'ready', lambda: False)

    response = client.post(
        '/studio/import',
        data={
            'platforms': 'tiktok', 
            'html_template': 'viral_editorial',
            'brand_label': '  Test Brand  ',
            'name': 'Viral Brand Test'
        },
        files={'video': ('source.mp4', source.read_bytes(), 'video/mp4')},
    )
    assert response.status_code == 200, response.text
    state = client.get('/studio/' + response.json()['project_id']).json()
    
    # Check that template and label are persisted
    gen = state['generation']
    assert gen['requested_template'] == 'viral_editorial'
    assert gen['branding'] == {
        'outro_enabled': True, 
        'outro_version': 'v1', 
        'brand_label': 'Test Brand'
    }
