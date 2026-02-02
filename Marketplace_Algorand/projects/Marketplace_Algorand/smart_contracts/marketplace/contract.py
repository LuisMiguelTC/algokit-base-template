from algopy import (
    Account, ARC4Contract, Asset, Global, Txn, UInt64, 
    arc4, gtxn, itxn, BoxMap
)

class ListingsData(arc4.Struct):
    owner: Account
    price: UInt64

class Marketplace(ARC4Contract):
    
    FEE_BPS: UInt64
    BPS_DIVISOR: UInt64
    active_listings: UInt64

    def __init__(self) -> None:
        # Creiamo una mappa nei Box: Chiave (ID Asset) -> Valore (Prezzo)
        self.listings = BoxMap(UInt64, ListingsData, key_prefix="")

    @arc4.abimethod(allow_actions=["NoOp"], create="require")
    def create_application(self) -> None:
        # Inizializzazione commissione del creatore
        self.FEE_BPS = UInt64(100)      # 100 BPS = 1%
        self.BPS_DIVISOR = UInt64(10_000)  # Divisore per basis points
        self.active_listings = UInt64(0)

    # 1. Metodo per mettere in vendita un nuovo oggetto
    @arc4.abimethod
    def list_asset(self, asset: UInt64, price: UInt64, mbr_pay: gtxn.PaymentTransaction) -> None:
        
        assert price > 0, "Il prezzo deve essere maggiore di zero"
        # Gestione del costo del Box (Minimum Balance Requirement)
        assert mbr_pay.amount >= 100_000, "Pagamento MBR insufficiente"
        # Il mittente del pagamento deve essere chi sta listando l'asset
        assert mbr_pay.sender == Txn.sender
        # Il destinatario del pagamento deve essere il contratto
        assert mbr_pay.receiver == Global.current_application_address
        # L'asset non deve essere già listato, questo impedisce 
        # l'overwrite di listings esistenti
        assert not self.listings.maybe(asset)[1], "Asset già listato"
        
        # Se il contratto non ha ancora l'asset, deve fare l'opt-in
        if not Global.current_application_address.is_opted_in(Asset(asset)):
            itxn.AssetTransfer(
                xfer_asset=asset,
                asset_receiver=Global.current_application_address,
                asset_amount=0,
                fee= 0, 
            ).submit()
        
        # Salviamo il prezzo nel Box associato all'ID dell'asset
        self.listings[asset] = ListingsData(
            owner=Txn.sender,
            price=price
        )
        self.active_listings += 1
        #return price
    
    @arc4.abimethod
    def deposit_asset(self, axfer: gtxn.AssetTransferTransaction) -> UInt64:
        """
        Accetta un trasferimento di asset e verifica l'incremento del magazzino.
        """
        # Verifichiamo che l'asset sia quello giusto
        asset_id = axfer.xfer_asset.id
        
        # Il destinatario deve essere il contratto
        assert (axfer.asset_receiver == Global.current_application_address), "Il ricevente deve essere il contratto"
        
        # L'asset deve essere tra quelli listati
        assert self.listings.maybe(asset_id)[1], "Asset non listato"
        
        # La quantità deve essere positiva
        assert axfer.asset_amount > 0, "La quantita deve essere maggiore di zero"

        # Solo il proprietario del listing può rifornire (opzionale)
        assert axfer.sender == self.listings[asset_id].owner, "Solo il proprietario può depositare"

        # L'incremento è avvenuto grazie ad 'axfer'. 
        # Restituiamo il bilancio totale aggiornato.
        return Asset(asset_id).balance(Global.current_application_address)

    # 2. Metodo per acquistare un oggetto specifico tra quelli listati
    @arc4.abimethod
    def buy_asset(self, asset_id: UInt64, quantity: UInt64, buyer_pay: gtxn.PaymentTransaction) -> None:
        # Verifica che l'asset sia listato
        assert self.listings.maybe(asset_id)[1], "Asset not listed"
        # Verifica che la quantità sia valida
        assert quantity > 0, "La quantità deve essere maggiore di zero"
        # Verifica quantità disponibile degli Assets richiesti
        # This check + Algorand atomicity guarantees safety under concurrent buys
        assert  Asset(asset_id).balance(Global.current_application_address) >= quantity
        # Validazione pagamento
        expected_amount = self.listings[asset_id].price * quantity
        assert buyer_pay.sender == Txn.sender
        # Il pagamento deve essere esattamente l'importo atteso
        assert buyer_pay.amount == expected_amount
        # Il destinatario del pagamento deve essere il contratto
        assert buyer_pay.receiver == Global.current_application_address

        # Calcolo fee usando FEE_BPS / BPS_DIVISOR
        fee = (expected_amount * self.FEE_BPS) // self.BPS_DIVISOR
        seller_amount = expected_amount - fee
        
        # Trasferimento ALGO al venditore
        itxn.Payment(
            receiver=self.listings[asset_id].owner,
            amount=seller_amount,
            fee=0,  # il contratto dirotta il pagamento al venditore
        ).submit()

        # Pagamento della commissione al creatore
        if fee > 0:
            itxn.Payment(
                receiver=Global.creator_address,
                amount=fee,
                fee=0,  # il contratto dirotta il pagamento al creatore
            ).submit()

        # Trasferimento dell'asset all'acquirente
        itxn.AssetTransfer(
            xfer_asset=asset_id,
            asset_receiver=Txn.sender,
            asset_amount=quantity
        ).submit()

    @arc4.abimethod
    def delist_remaining_assets(self, asset_id: UInt64) -> None:
        
        assert self.listings.maybe(asset_id)[1], "Asset not listed"
        # Solo il proprietario originale può ritirare i suoi asset
        assert Txn.sender == self.listings[asset_id].owner
        # Trasferisce tutti gli asset del contratto al proprietario
        contract_balance = Asset(asset_id).balance(Global.current_application_address)
        # Non viene messo nessun vincolo sul fatto che ci siano o meno asset residui
        # Perchè il venditore potrebbe voler ritirare l'asset anche se è esaurito
        # Eliminando quindi semplicemente il listing
        
        itxn.AssetTransfer(
            xfer_asset=asset_id,
            asset_receiver=self.listings[asset_id].owner,
            asset_amount=contract_balance,
            asset_close_to=self.listings[asset_id].owner, # <--- QUESTO chiude l'opt-in dell'asset!
            fee=0,
        ).submit()
    
        # Rimuoviamo il listing dalla BoxMap
        del self.listings[asset_id]
        # Decrementiamo il contatore dei listing attivi
        self.active_listings -= 1

    @arc4.abimethod(allow_actions=["DeleteApplication"])
    def delete_application(self) -> None:
        # Solo il creatore può eliminare l'applicazione
        assert Txn.sender == Global.creator_address
        # Nessun listing attivo deve essere presente
        assert self.active_listings == 0, "Active listings must be zero"
        # Tutti i fondi residui verranno inviati al creatore automaticamente
        
        itxn.Payment(
            receiver=Global.creator_address,
            close_remainder_to=Global.creator_address,
            amount=0,
            fee=0,
        ).submit()